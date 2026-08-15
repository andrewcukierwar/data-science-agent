"""Deterministic construction, evidence, and persistence tests."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents import (
    STATISTICIAN_OBJECTIVE,
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    StatisticianEvidenceError,
    build_statistician_agent,
    run_statistician,
    validate_statistician_result,
)
from agents.statistician import Runner
from orchestration.ledger import AnalysisLedger
from schemas.findings import ConfidenceLevel, Finding, SpecialistResult
from schemas.run_state import ToolEvent, ToolEventStatus
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager


def test_statistician_is_structured_python_only_and_cannot_delegate() -> None:
    agent = build_statistician_agent(model="test-model")

    assert agent.name == "Statistician"
    assert agent.model == "test-model"
    assert agent.output_type is SpecialistResult
    assert agent.handoffs == []
    assert [tool.name for tool in agent.tools] == ["read_document", "run_python"]


def test_statistician_rejects_non_statistician_configuration() -> None:
    config = AgentRunConfig(
        run_id="run-statistician",
        agent_role=AgentRole.ANALYST,
        model="test-model",
    )

    with pytest.raises(ValueError, match="Statistician"):
        build_statistician_agent(config)


def test_statistician_instructions_cover_inferential_procedure() -> None:
    agent = build_statistician_agent(model="test-model")
    instructions = agent.instructions.lower()

    for term in (
        "hypothesis",
        "test",
        "assumptions",
        "confidence intervals",
        "effect sizes",
        "practical",
        "multiple comparisons",
        "causality",
        "not a user-facing",
        "delegate",
        "evidence_refs",
    ):
        assert term in instructions


def _context(tmp_path: Path) -> AgentRunContext:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-statistician"
    )
    ledger = AnalysisLedger(workspace, objective=STATISTICIAN_OBJECTIVE)
    return AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id="run-statistician",
            agent_role=AgentRole.STATISTICIAN,
            model="test-model",
        ),
    )


def _result() -> SpecialistResult:
    return SpecialistResult(
        objective="Assess the difference between two samples.",
        findings=[
            Finding(
                id="S001",
                statement="The estimated difference is statistically significant.",
                metric="mean_difference",
                value=0.8,
                evidence_refs=["working/scripts/stats.py"],
                confidence=ConfidenceLevel.HIGH,
            )
        ],
        artifacts=["working/scripts/stats.py"],
        methods_used=["Welch t-test", "95% confidence interval"],
    )


def test_statistician_requires_executed_evidence(tmp_path: Path) -> None:
    context = _context(tmp_path)

    with pytest.raises(StatisticianEvidenceError, match="S001"):
        validate_statistician_result(_result(), context.ledger)


def test_statistician_persists_findings_and_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    script = context.workspace.working / "scripts" / "stats.py"
    script.write_text("print('statistical result')\n", encoding="utf-8")
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    context.ledger.append_tool_event(
        ToolEvent(
            id="tool-stats",
            tool_name="run_python",
            status=ToolEventStatus.SUCCEEDED,
            started_at=timestamp,
            completed_at=timestamp,
            artifact_refs=["working/scripts/stats.py"],
        )
    )
    result = _result()

    async def fake_run(agent, objective, *, context, **kwargs):  # noqa: ANN001
        assert agent.output_type is SpecialistResult
        assert objective == STATISTICIAN_OBJECTIVE
        assert context is not None
        return SimpleNamespace(final_output=result)

    monkeypatch.setattr(Runner, "run", fake_run)

    returned = asyncio.run(run_statistician(context))
    reloaded = AnalysisLedger(context.ledger.state_path)

    assert returned == result
    assert reloaded.findings == result.findings
    assert reloaded.artifacts[0].path == "working/scripts/stats.py"
    assert reloaded.artifacts[0].kind.value == "script"
    assert context.ledger.budget.specialist_invocations == 1
