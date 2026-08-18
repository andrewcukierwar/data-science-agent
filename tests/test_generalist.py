"""Deterministic construction, permission, and contract tests for the baseline."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import agents.generalist as generalist_module
from agents import (
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    PermissionDeniedError,
    build_generalist_agent,
    run_generalist,
)
from orchestration.generalist_runner import GeneralistRunner
from orchestration.ledger import AnalysisLedger
from schemas.audit import AuditResult, AuditStatus
from schemas.findings import ConfidenceLevel, Finding
from schemas.generalist import GeneralistResult
from schemas.lead import LeadResult
from schemas.metrics import MetricComparison, MetricComparisonType
from schemas.run_state import ToolEvent, ToolEventStatus
from schemas.validation import ValidationResult, ValidationStatus
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import Workspace, WorkspaceManager

_STAMP = datetime(2026, 1, 1, tzinfo=UTC)


def _context(tmp_path: Path) -> AgentRunContext:
    input_source = tmp_path / "input-source"
    input_source.mkdir()
    pd.DataFrame({"observed_value": [1]}).to_parquet(
        input_source / "customers.parquet",
        index=False,
    )
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-generalist",
        inputs_source=input_source,
    )
    evidence_path = workspace.working / "queries" / "evidence.sql"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        "SELECT COUNT(*) AS observed_value FROM customers;\n",
        encoding="utf-8",
    )
    ledger = AnalysisLedger(workspace, objective="Explain the observed change.")
    ledger.append_tool_event(
        ToolEvent(
            id="tool-evidence",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=_STAMP,
            completed_at=_STAMP,
            arguments={"query_id": "Q-evidence"},
            output={"rows": [{"observed_value": 1}]},
            artifact_refs=["working/queries/evidence.sql"],
        )
    )
    return AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id="run-generalist",
            agent_role=AgentRole.GENERALIST,
            model="test-model",
        ),
    )


def _result() -> GeneralistResult:
    comparison = MetricComparison(
        metric_key="revenue",
        baseline_period="Q1",
        comparison_period="Q2",
        comparison_type=MetricComparisonType.RELATIVE_CHANGE,
        value=0.1,
        unit="relative_change_fraction",
        evidence_refs=["tool-evidence"],
    )
    finding = Finding(
        id="F1",
        statement="Revenue increased by 10 percent.",
        metric="revenue",
        value=0.1,
        value_unit="relative_change_fraction",
        evidence_refs=["tool-evidence"],
        confidence=ConfidenceLevel.MEDIUM,
    )
    return GeneralistResult(
        audit=AuditResult(status=AuditStatus.COMPLETE, audited_at=_STAMP),
        candidate=LeadResult(
            objective="Explain the observed change.",
            answer="Revenue increased by 10 percent in the observed comparison.",
            findings=[finding],
            metric_comparisons=[comparison],
        ),
        validation=ValidationResult(
            status=ValidationStatus.PASS,
            checked_finding_ids=["F1"],
            summary="The candidate is supported by the executed evidence.",
        ),
    )


def test_generalist_has_all_primitives_but_no_specialist_surface() -> None:
    agent = build_generalist_agent(model="test-model")
    names = [tool.name for tool in agent.tools]

    assert agent.name == "Generalist Data Scientist"
    assert agent.model == "test-model"
    assert agent.output_type.output_type is GeneralistResult
    assert agent.output_type.is_strict_json_schema() is False
    assert agent.handoffs == []
    assert names == [
        "inspect_workspace",
        "read_document",
        "inspect_relations",
        "run_sql",
        "run_python",
        "save_artifact",
        "inspect_evidence",
        "update_investigation_plan",
        "record_hypothesis",
        "record_open_question",
    ]
    assert not any(name.startswith("delegate_to_") for name in names)
    assert not any(name in {"run_lead", "run_critic"} for name in names)


def test_generalist_config_and_context_cannot_use_specialist_calls(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="generalist role"):
        build_generalist_agent(
            AgentRunConfig(
                run_id="wrong-role",
                agent_role=AgentRole.LEAD,
                model="test-model",
            )
        )

    context = _context(tmp_path)
    with pytest.raises(PermissionDeniedError, match="delegate_to_analyst"):
        context.require_permission("delegate_to_analyst")
    context.require_permission("run_sql")
    assert context.ledger.budget.specialist_invocations == 0
    runner = GeneralistRunner(workspace_base_dir=tmp_path / "runner-workspaces")
    with pytest.raises(ValueError, match="only a generalist"):
        runner._agent_context(context.workspace, context.ledger, AgentRole.LEAD)


def test_generalist_run_uses_bounded_turns_and_shared_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    expected = _result()

    async def fake_run(agent, prompt, *, context, **kwargs):  # noqa: ANN001
        assert agent.output_type.output_type is GeneralistResult
        assert context.agent_role is AgentRole.GENERALIST
        assert kwargs["max_turns"] == 16
        assert "ground_truth" not in prompt
        return SimpleNamespace(final_output=expected)

    monkeypatch.setattr(generalist_module.Runner, "run", fake_run)
    returned = asyncio.run(run_generalist(context, "Explain the observed change."))

    assert returned.candidate.metric_comparisons == context.ledger.metric_comparisons
    assert returned.validation in context.ledger.validation_results
    assert context.ledger.findings == returned.candidate.findings
    assert context.ledger.audit == returned.audit
    assert context.ledger.budget.specialist_invocations == 0
    assert context.ledger.budget.sql_executions == 0
    assert context.ledger.budget.python_executions == 0


def test_generalist_runner_reuses_report_contract_without_specialist_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_source = tmp_path / "input-source"
    input_source.mkdir()
    pd.DataFrame({"observed_value": [1]}).to_parquet(
        input_source / "customers.parquet",
        index=False,
    )
    workspace: Workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-generalist-runner",
        inputs_source=input_source,
    )
    evidence = workspace.working / "queries" / "evidence.sql"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("SELECT COUNT(*) FROM customers;\n", encoding="utf-8")
    ledger = AnalysisLedger(workspace, objective="Explain the observed change.")
    ledger.append_tool_event(
        ToolEvent(
            id="tool-evidence",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=_STAMP,
            completed_at=_STAMP,
            output={"rows": [{"value": 1}]},
            artifact_refs=["working/queries/evidence.sql"],
        )
    )
    expected = _result()

    async def fake_run(context, objective, *, business_context, agent):  # noqa: ANN001
        assert context.agent_role is AgentRole.GENERALIST
        assert agent.handoffs == []
        assert objective == "Explain the observed change."
        return expected

    monkeypatch.setattr(
        "orchestration.generalist_runner.run_generalist",
        fake_run,
    )
    runner = GeneralistRunner(
        workspace_base_dir=tmp_path / "workspaces",
        model="test-model",
        model_provider="test-provider",
    )
    result = asyncio.run(
        runner.run(
            "run-generalist-runner",
            "Explain the observed change.",
            workspace=workspace,
        )
    )

    assert result.status.value == "completed"
    assert result.report is not None
    assert result.report.path == "outputs/report.md"
    assert result.ledger is not None
    assert result.ledger.specialist_results == []
    assert all(
        event.agent_role == AgentRole.GENERALIST.value
        for event in result.ledger.agent_events
    )
    assert result.generalist_result is not None
    assert (
        result.ledger.metric_comparisons
        == result.generalist_result.candidate.metric_comparisons
    )
    assert "Revenue increased" in (workspace.outputs / "report.md").read_text()
