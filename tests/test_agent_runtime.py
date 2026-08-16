"""Tests for the shared Agents SDK context, tools, and run budgets."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agents.tool_context import ToolContext

from agents import (
    DEFAULT_AGENT_TURN_LIMITS,
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    ToolOutputText,
    ToolResponse,
    build_agent,
    inspect_evidence,
    inspect_relations,
    inspect_workspace,
    read_document,
    run_python,
    run_sql,
    save_artifact,
    tools_for_role,
)
from orchestration.budgets import BudgetExhaustedError
from orchestration.ledger import AnalysisLedger
from sandbox.executor import SandboxExecutionResult
from schemas.run_state import ArtifactKind, RunBudget, ToolEvent, ToolEventStatus
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager


def test_agent_turn_limits_are_role_specific_and_configurable() -> None:
    for role, expected in DEFAULT_AGENT_TURN_LIMITS.items():
        config = AgentRunConfig(
            run_id=f"run-{role.value}",
            agent_role=role,
            model="test-model",
        )
        assert config.turn_limit == expected
        assert config.turn_limit_for(role) == expected

    configured = AgentRunConfig(
        run_id="run-custom-turns",
        agent_role=AgentRole.LEAD,
        model="test-model",
        agent_turn_limits={
            AgentRole.LEAD: 20,
            AgentRole.CRITIC: 4,
        },
    )
    assert configured.turn_limit == 20
    assert configured.turn_limit_for(AgentRole.CRITIC) == 4
    assert configured.turn_limit_for(AgentRole.ANALYST) == 10

    with pytest.raises(ValueError):
        AgentRunConfig(
            run_id="run-invalid-turns",
            agent_role=AgentRole.LEAD,
            agent_turn_limits={AgentRole.LEAD: 0},
        )


class FakeExecutor:
    """Deterministic Python executor for SDK tool tests."""

    timeout_seconds = 30.0
    memory_limit = "512m"
    cpu_limit = 1.0
    pids_limit = 128

    def __init__(self, result: SandboxExecutionResult | None = None) -> None:
        self.result = result or SandboxExecutionResult(
            success=True,
            stdout="python output\n",
            exit_code=0,
            duration_seconds=0.1,
        )

    def execute(
        self,
        script_path: str,
        *,
        timeout_seconds: float | None = None,
    ) -> SandboxExecutionResult:
        return self.result


def _context(
    tmp_path: Path,
    role: AgentRole = AgentRole.ANALYST,
    *,
    budget: RunBudget | None = None,
    max_result_rows: int = 100,
    max_text_chars: int = 4_000,
) -> AgentRunContext:
    tmp_path.mkdir(parents=True, exist_ok=True)
    input_source = tmp_path / "input-source"
    docs_source = tmp_path / "docs-source"
    input_source.mkdir()
    docs_source.mkdir()
    (input_source / "orders.csv").write_text(
        "customer_id,revenue\nC001,12.50\n",
        encoding="utf-8",
    )
    (docs_source / "business_definitions.md").write_text(
        "# Definitions\n\nProfit is net revenue minus COGS.\n",
        encoding="utf-8",
    )
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-tools",
        inputs_source=input_source,
        docs_source=docs_source,
    )
    ledger = AnalysisLedger(workspace, objective="Test deterministic tools.")
    if budget is not None:
        ledger.update_budget(budget)
    sql_service = DuckDBExecutionService(workspace, ledger)
    python_service = PythonExecutionService(
        workspace,
        ledger,
        executor=FakeExecutor(),
    )
    artifact_manager = ArtifactManager(workspace, ledger)
    return AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=sql_service,
        python_service=python_service,
        artifact_manager=artifact_manager,
        run_config=AgentRunConfig(
            run_id="run-tools",
            agent_role=role,
            max_result_rows=max_result_rows,
            max_text_chars=max_text_chars,
        ),
    )


def _invoke(tool, context: AgentRunContext, arguments: dict | None = None):  # noqa: ANN001
    arguments = arguments or {}
    payload = json.dumps(arguments)
    wrapper = ToolContext(
        context,
        tool_name=tool.name,
        tool_call_id=f"call-{tool.name}",
        tool_arguments=payload,
    )
    result = asyncio.run(tool.on_invoke_tool(wrapper, payload))
    if isinstance(result, ToolOutputText):
        return ToolResponse.model_validate_json(result.text)
    return result


def test_role_tool_surfaces_preserve_project_plan_permissions() -> None:
    assert [tool.name for tool in tools_for_role(AgentRole.LEAD)] == [
        "inspect_workspace",
        "read_document",
        "save_artifact",
    ]
    assert [tool.name for tool in tools_for_role(AgentRole.DATA_AUDITOR)] == [
        "inspect_workspace",
        "read_document",
        "inspect_relations",
        "run_sql",
        "run_python",
    ]
    assert "run_sql" not in [
        tool.name for tool in tools_for_role(AgentRole.STATISTICIAN)
    ]
    assert "inspect_relations" in [
        tool.name for tool in tools_for_role(AgentRole.ANALYST)
    ]
    assert "save_artifact" not in [
        tool.name for tool in tools_for_role(AgentRole.CRITIC)
    ]
    assert "inspect_evidence" in [
        tool.name for tool in tools_for_role(AgentRole.CRITIC)
    ]
    assert "inspect_evidence" not in [
        tool.name for tool in tools_for_role(AgentRole.LEAD)
    ]

    agent = build_agent("Lead", AgentRole.LEAD, model="test-model")
    assert agent.model == "test-model"
    assert [tool.name for tool in agent.tools] == [
        "inspect_workspace",
        "read_document",
        "save_artifact",
    ]


def test_tools_bind_to_context_and_return_bounded_structured_results(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, max_result_rows=2, max_text_chars=256)

    workspace_result = _invoke(inspect_workspace, context)
    assert isinstance(workspace_result, ToolResponse)
    assert workspace_result.success is True
    assert workspace_result.data["run_id"] == "run-tools"
    assert workspace_result.data["files"]
    assert all(
        not item["path"].startswith(("state/", "logs/"))
        for item in workspace_result.data["files"]
    )

    document_result = _invoke(
        read_document, context, {"path": "business_definitions.md"}
    )
    assert document_result.success is True
    assert document_result.data["path"] == "docs/business_definitions.md"
    assert "Profit" in document_result.data["content"]

    relations_result = _invoke(inspect_relations, context)
    assert relations_result.success is True
    assert relations_result.data["relations"] == []
    assert relations_result.data["total_relations"] == 0
    assert context.ledger.budget.sql_executions == 1

    sql_result = _invoke(
        run_sql,
        context,
        {
            "sql": "SELECT value FROM range(500) AS generated(value)",
            "query_id": "Q-BOUNDED",
        },
    )
    assert sql_result.success is True
    assert len(sql_result.data["rows"]) == 2
    assert sql_result.data["row_count"] == 500
    assert sql_result.data["model_rows_truncated"] is True
    assert context.ledger.budget.sql_executions == 2

    context.python_service.executor.result = SandboxExecutionResult(
        success=True,
        stdout="x" * 1_000,
        exit_code=0,
        duration_seconds=0.1,
    )
    python_result = _invoke(
        run_python,
        context,
        {"source": "print('ok')", "script_id": "P-BOUNDED"},
    )
    assert python_result.success is True
    assert len(python_result.data["stdout"]) == 256
    assert python_result.data["stdout_truncated"] is True
    assert context.ledger.budget.python_executions == 1


def test_python_tool_documents_separate_data_access_environment() -> None:
    description = run_python.description.lower()

    assert "separate isolated container" in description
    assert "does not inherit" in description
    assert "/workspace/inputs" in description
    assert "registered sql views" in description


def test_forbidden_tool_returns_permission_error_without_side_effects(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, AgentRole.LEAD)

    result = _invoke(
        run_sql,
        context,
        {"sql": "SELECT 1", "query_id": "Q-FORBIDDEN"},
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "permission_denied"
    assert context.ledger.budget.sql_executions == 0
    assert not (context.workspace.working / "queries/Q-FORBIDDEN.sql").exists()


def test_critic_inspect_evidence_resolves_events_artifacts_and_safe_paths(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, AgentRole.CRITIC, max_text_chars=256)
    query = context.workspace.working / "queries" / "Q-EVIDENCE.sql"
    query.parent.mkdir(parents=True, exist_ok=True)
    query.write_text("SELECT 1 AS value;\n", encoding="utf-8")
    context.ledger.append_tool_event(
        ToolEvent(
            id="tool-Q-EVIDENCE",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            completed_at=datetime(2026, 1, 1, tzinfo=UTC),
            arguments={"query_id": "Q-EVIDENCE"},
            output={"columns": ["value"], "rows": [[1]], "row_count": 1},
            artifact_refs=["working/queries/Q-EVIDENCE.sql"],
        )
    )
    artifact = context.workspace.outputs / "evidence.txt"
    artifact.write_text("documented evidence\n", encoding="utf-8")
    context.artifact_manager.register(
        "outputs/evidence.txt",
        artifact_id="A-EVIDENCE",
        kind=ArtifactKind.OTHER,
    )

    event_result = _invoke(
        inspect_evidence,
        context,
        {"reference": "tool-Q-EVIDENCE"},
    )
    assert event_result.success is True
    assert event_result.data["reference_type"] == "tool_event"
    assert event_result.data["output"]["row_count"] == 1
    assert event_result.data["artifact_refs"] == ["working/queries/Q-EVIDENCE.sql"]

    artifact_result = _invoke(
        inspect_evidence,
        context,
        {"reference": "A-EVIDENCE"},
    )
    assert artifact_result.success is True
    assert artifact_result.data["reference_type"] == "artifact"
    assert artifact_result.data["provenance_verified"] is True
    assert artifact_result.data["content"] == "documented evidence\n"

    path_result = _invoke(
        inspect_evidence,
        context,
        {"reference": "working/queries/Q-EVIDENCE.sql"},
    )
    assert path_result.success is True
    assert path_result.data["reference_type"] == "workspace_file"
    assert path_result.data["path"] == "working/queries/Q-EVIDENCE.sql"

    traversal_result = _invoke(
        inspect_evidence,
        context,
        {"reference": "working/queries/../Q-EVIDENCE.sql"},
    )
    assert traversal_result.success is False

    lead_context = _context(tmp_path / "lead", AgentRole.LEAD)
    forbidden = _invoke(
        inspect_evidence,
        lead_context,
        {"reference": "tool-Q-EVIDENCE"},
    )
    assert forbidden.success is False
    assert forbidden.error is not None
    assert forbidden.error.code == "permission_denied"


def test_save_artifact_enforces_chart_budget_and_records_provenance(
    tmp_path: Path,
) -> None:
    context = _context(
        tmp_path,
        budget=RunBudget(max_charts=1),
    )
    chart = context.workspace.outputs / "charts" / "chart.html"
    chart.write_text("<html>chart</html>", encoding="utf-8")

    first = _invoke(
        save_artifact,
        context,
        {
            "path": "outputs/charts/chart.html",
            "artifact_id": "CHART-1",
            "kind": ArtifactKind.CHART.value,
            "media_type": "text/html",
        },
    )
    assert first.success is True
    assert context.ledger.budget.charts_created == 1
    assert context.ledger.get_artifact("CHART-1") is not None

    second_chart = context.workspace.outputs / "charts" / "chart-2.html"
    second_chart.write_text("<html>chart 2</html>", encoding="utf-8")
    second = _invoke(
        save_artifact,
        context,
        {
            "path": "outputs/charts/chart-2.html",
            "artifact_id": "CHART-2",
            "kind": ArtifactKind.CHART.value,
        },
    )
    assert second.success is False
    assert second.error is not None
    assert second.error.code == "budget_exhausted"
    assert context.ledger.get_artifact("CHART-2") is None


def test_sql_python_and_specialist_critic_budgets_stop_before_work(
    tmp_path: Path,
) -> None:
    context = _context(
        tmp_path,
        budget=RunBudget(
            max_specialist_invocations=1,
            max_sql_executions=1,
            max_python_executions=1,
            max_critic_loops=1,
        ),
    )

    assert _invoke(
        run_sql,
        context,
        {"sql": "SELECT 1", "query_id": "Q-1"},
    ).success
    second_sql = _invoke(
        run_sql,
        context,
        {"sql": "SELECT 2", "query_id": "Q-2"},
    )
    assert second_sql.error is not None
    assert second_sql.error.code == "budget_exhausted"
    assert not (context.workspace.working / "queries/Q-2.sql").exists()

    assert _invoke(
        run_python,
        context,
        {"source": "pass", "script_id": "P-1"},
    ).success
    second_python = _invoke(
        run_python,
        context,
        {"source": "pass", "script_id": "P-2"},
    )
    assert second_python.error is not None
    assert second_python.error.code == "budget_exhausted"
    assert not (context.workspace.working / "scripts/P-2.py").exists()

    context.record_specialist_invocation()
    with pytest.raises(BudgetExhaustedError, match="specialist_invocations"):
        context.record_specialist_invocation()
    context.record_critic_loop()
    with pytest.raises(BudgetExhaustedError, match="critic_loops"):
        context.record_critic_loop()

    assert context.ledger.budget.specialist_invocations == 1
    assert context.ledger.budget.critic_loops == 1


def test_context_rejects_services_bound_to_another_run(tmp_path: Path) -> None:
    first = _context(tmp_path)
    second = _context(tmp_path / "second")

    with pytest.raises(ValueError, match="different workspace"):
        AgentRunContext(
            workspace=first.workspace,
            ledger=first.ledger,
            sql_service=second.sql_service,
            python_service=first.python_service,
            artifact_manager=first.artifact_manager,
            run_config=first.run_config,
        )
