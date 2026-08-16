"""Deterministic Phase 0 workspace-to-ledger acceptance coverage."""

import json
from hashlib import sha256
from pathlib import Path

from orchestration.ledger import AnalysisLedger
from sandbox.executor import DockerSandboxExecutor
from scenarios.generator import SyntheticEcommerceConfig, SyntheticEcommerceGenerator
from schemas.run_state import ArtifactKind, ToolEventStatus
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager


def test_phase0_sql_python_artifact_ledger_flow(
    tmp_path: Path,
    docker_image: str,
) -> None:
    input_source = tmp_path / "input-source"
    docs_source = tmp_path / "docs-source"
    input_source.mkdir()
    docs_source.mkdir()

    dataset = SyntheticEcommerceGenerator(
        SyntheticEcommerceConfig(
            seed=17,
            num_customers=8,
            num_orders=24,
            num_sessions=32,
            num_products=3,
            period_days=14,
        )
    ).generate()
    dataset.write(input_source)
    (docs_source / "business_definitions.md").write_text(
        dataset.business_definitions,
        encoding="utf-8",
    )

    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-phase0",
        inputs_source=input_source,
        docs_source=docs_source,
    )
    ledger = AnalysisLedger(workspace, objective="Summarize seeded order revenue.")

    orders_path = workspace.inputs / "orders.parquet"
    sql_service = DuckDBExecutionService(workspace, ledger, max_rows=10)
    sql_result = sql_service.execute(
        "SELECT COUNT(*) AS order_count, "
        "ROUND(SUM(net_revenue), 2) AS net_revenue "
        f"FROM read_parquet('{orders_path}')",
        query_id="Q-PHASE0",
    )

    assert sql_result.success is True
    assert sql_result.truncated is False
    assert sql_result.row_count == 1
    sql_summary = {
        "order_count": int(sql_result.rows[0][0]),
        "net_revenue": round(float(sql_result.rows[0][1]), 2),
    }
    (workspace.working / "sql_summary.json").write_text(
        json.dumps(sql_summary, sort_keys=True),
        encoding="utf-8",
    )

    python_service = PythonExecutionService(
        workspace,
        ledger,
        executor=DockerSandboxExecutor(
            workspace,
            image=docker_image,
            timeout_seconds=20,
        ),
    )
    python_source = """
import json
from pathlib import Path

import pandas as pd


sql_summary = json.loads(
    Path("/workspace/working/sql_summary.json").read_text(encoding="utf-8")
)
orders = pd.read_parquet("/workspace/inputs/orders.parquet")
summary = {
    "order_count": int(len(orders)),
    "net_revenue": round(float(orders["net_revenue"].sum()), 2),
}
summary["matches_sql"] = summary == sql_summary
if not summary["matches_sql"]:
    raise RuntimeError("Python summary did not match SQL summary")

Path("/workspace/outputs/summary.json").write_text(
    json.dumps(summary, sort_keys=True),
    encoding="utf-8",
)
print(json.dumps(summary, sort_keys=True))
"""
    python_result = python_service.run_python(
        python_source,
        script_id="P-PHASE0",
    )

    assert python_result.success is True, python_result.error or python_result.stderr
    output_path = workspace.outputs / "summary.json"
    assert output_path.is_file()

    artifact_manager = ArtifactManager(workspace, ledger)
    artifact = artifact_manager.register(
        "outputs/summary.json",
        artifact_id="A-PHASE0",
        kind=ArtifactKind.REPORT,
        media_type="application/json",
        description="Seeded SQL/Python order revenue summary.",
    )
    assert artifact_manager.verify_artifact(artifact.id) is True

    reloaded = AnalysisLedger(ledger.state_path)
    persisted_artifact = reloaded.get_artifact(artifact.id)
    assert persisted_artifact is not None
    reloaded_artifact_manager = ArtifactManager(workspace, reloaded)
    assert reloaded_artifact_manager.verify_artifact(artifact.id) is True
    output_bytes = output_path.read_bytes()
    assert persisted_artifact.sha256 == sha256(output_bytes).hexdigest()
    assert persisted_artifact.size_bytes == len(output_bytes)
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "matches_sql": True,
        **sql_summary,
    }

    events = {event.tool_name: event for event in reloaded.tool_events}
    assert set(events) == {"run_sql", "run_python"}
    assert events["run_sql"].status is ToolEventStatus.SUCCEEDED
    assert events["run_python"].status is ToolEventStatus.SUCCEEDED
    assert events["run_sql"].artifact_refs == ["working/queries/Q-PHASE0.sql"]
    assert events["run_python"].artifact_refs == [
        "working/scripts/P-PHASE0.py",
        "outputs/summary.json",
    ]
    assert events["run_python"].output is not None
    assert events["run_python"].output["generated_evidence_refs"] == [
        "outputs/summary.json"
    ]
    assert events["run_sql"].output is not None
    assert events["run_sql"].output["truncated"] is False
    assert reloaded.budget.sql_executions == 1
    assert reloaded.budget.python_executions == 1
    assert persisted_artifact.path == "outputs/summary.json"
