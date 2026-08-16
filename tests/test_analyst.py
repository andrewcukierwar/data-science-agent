"""Deterministic construction and evidence-contract tests for the Analyst."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents import (
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    AnalystEvidenceError,
    build_analyst_agent,
    run_analyst,
    validate_analyst_result,
)
from agents.analyst import Runner
from orchestration.ledger import AnalysisLedger
from schemas.findings import ConfidenceLevel, Finding, SpecialistResult
from schemas.run_state import ToolEvent, ToolEventStatus
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager


def test_analyst_is_structured_and_cannot_delegate() -> None:
    agent = build_analyst_agent(model="test-model")

    assert agent.name == "Analyst"
    assert agent.model == "test-model"
    assert agent.output_type is SpecialistResult
    assert agent.handoffs == []
    assert [tool.name for tool in agent.tools] == [
        "inspect_workspace",
        "read_document",
        "run_sql",
        "run_python",
        "save_artifact",
    ]


def test_analyst_rejects_non_analyst_run_configuration() -> None:
    config = AgentRunConfig(
        run_id="run-analyst",
        agent_role=AgentRole.DATA_AUDITOR,
        model="test-model",
    )

    with pytest.raises(ValueError, match="Analyst"):
        build_analyst_agent(config)


def test_analyst_instructions_cover_procedure_and_evidence_contract() -> None:
    agent = build_analyst_agent(model="test-model")
    instructions = agent.instructions.lower()

    for term in (
        "kpi",
        "segment",
        "cac",
        "ltv",
        "cohort",
        "funnel",
        "period",
        "definitions",
        "causal",
        "follow_up_questions",
        "evidence_refs",
        "cannot delegate",
    ):
        assert term in instructions


def _ledger(tmp_path: Path) -> AnalysisLedger:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-analyst"
    )
    ledger = AnalysisLedger(workspace, objective="Test Analyst evidence.")
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    ledger.append_tool_event(
        ToolEvent(
            id="tool-Q001",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=timestamp,
            completed_at=timestamp,
            artifact_refs=["working/queries/Q001.sql"],
        )
    )
    return ledger


def _quantitative_result(evidence_ref: str) -> SpecialistResult:
    return SpecialistResult(
        objective="Compare the profitability periods.",
        findings=[
            Finding(
                id="F001",
                statement="Meta CAC increased.",
                metric="cac",
                value=1.3,
                evidence_refs=[evidence_ref],
                confidence=ConfidenceLevel.MEDIUM,
            )
        ],
    )


def test_material_finding_evidence_must_match_ledger_execution(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)

    valid = _quantitative_result("working/queries/Q001.sql")
    assert validate_analyst_result(valid, ledger) == valid

    invalid = _quantitative_result("working/queries/not-executed.sql")
    with pytest.raises(AnalystEvidenceError, match="F001"):
        validate_analyst_result(invalid, ledger)


def test_analyst_runner_uses_the_role_specific_turn_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path)
    workspace = WorkspaceManager(tmp_path / "workspaces").open_workspace("run-analyst")
    context = AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id="run-analyst",
            agent_role=AgentRole.ANALYST,
            model="test-model",
        ),
    )

    async def fake_run(agent, objective, *, context, **kwargs):  # noqa: ANN001
        assert kwargs["max_turns"] == 10
        return SimpleNamespace(
            final_output=_quantitative_result("working/queries/Q001.sql")
        )

    monkeypatch.setattr(Runner, "run", fake_run)

    result = asyncio.run(run_analyst(context, "Compare the periods."))

    assert result.findings[0].id == "analyst:F001"


def test_specialist_result_requires_nonempty_finding_evidence_refs() -> None:
    with pytest.raises(ValueError):
        Finding(
            id="F001",
            statement="A quantitative finding.",
            value=1.0,
            evidence_refs=[],
            confidence=ConfidenceLevel.LOW,
        )
