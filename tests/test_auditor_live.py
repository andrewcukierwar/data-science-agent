"""Opt-in live Data Auditor coverage on clean and canonical data."""

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from agents import AgentRole, AgentRunConfig, AgentRunContext, run_data_auditor
from orchestration.ledger import AnalysisLedger
from scenarios.generator import (
    SyntheticEcommerceConfig,
    SyntheticEcommerceGenerator,
)
from scenarios.injection import generate_canonical_profitability_scenario
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager

pytestmark = pytest.mark.live


@pytest.mark.parametrize("dataset_kind", ["clean", "canonical"])
@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY is not set; live tests are opt-in",
)
@pytest.mark.skipif(
    not os.getenv("OPENAI_DEFAULT_MODEL"),
    reason="OPENAI_DEFAULT_MODEL is not set; live tests are opt-in",
)
def test_data_auditor_live_does_not_invent_quality_issues(
    tmp_path: Path,
    docker_image: str,
    dataset_kind: str,
) -> None:
    config = SyntheticEcommerceConfig(
        seed=42,
        num_customers=1_000,
        num_orders=4_000,
        num_sessions=8_000,
        num_products=4,
        period_days=365,
    )
    if dataset_kind == "clean":
        dataset = SyntheticEcommerceGenerator(config).generate()
    else:
        dataset = generate_canonical_profitability_scenario(config).dataset

    generated = dataset.write(tmp_path / "generated")
    inputs = tmp_path / "inputs"
    docs = tmp_path / "docs"
    inputs.mkdir()
    docs.mkdir()
    for name in ("customers", "orders", "sessions", "marketing_spend"):
        shutil.copy2(generated[name], inputs / generated[name].name)
    shutil.copy2(generated["business_definitions"], docs / "business_definitions.md")

    run_id = f"run-auditor-live-{dataset_kind}"
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        run_id,
        inputs_source=inputs,
        docs_source=docs,
    )
    ledger = AnalysisLedger(workspace, objective="Perform a complete preflight audit.")
    context = AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(
            workspace,
            ledger,
            image=docker_image,
        ),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id=run_id,
            agent_role=AgentRole.DATA_AUDITOR,
            model=os.environ["OPENAI_DEFAULT_MODEL"],
        ),
    )

    audit = asyncio.run(run_data_auditor(context))

    assert audit.tables
    assert audit.issues == [], audit.model_dump(mode="json")
    assert context.ledger.audit == audit
    # R17: a live agent call that recorded no usage is not a valid smoke run.
    assert context.ledger.usage.requests > 0
    assert context.ledger.usage.total_tokens > 0
    assert context.ledger.usage_complete is True
