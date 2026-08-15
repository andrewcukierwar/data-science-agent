"""Deterministic construction, candidate, and persistence tests."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents import (
    CRITIC_OBJECTIVE,
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    build_critic_agent,
    run_critic,
)
from agents.critic import Runner
from orchestration.ledger import AnalysisLedger
from schemas.findings import ConfidenceLevel, Finding
from schemas.run_state import RunBudget
from schemas.validation import (
    CriticCandidate,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
    ValidationStatus,
)
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager


def test_critic_is_structured_and_cannot_delegate() -> None:
    agent = build_critic_agent(model="test-model")

    assert agent.name == "Critic"
    assert agent.model == "test-model"
    assert agent.output_type is ValidationResult
    assert agent.handoffs == []
    assert [tool.name for tool in agent.tools] == [
        "inspect_workspace",
        "read_document",
        "run_sql",
        "run_python",
    ]


def test_critic_rejects_non_critic_configuration() -> None:
    config = AgentRunConfig(
        run_id="run-critic",
        agent_role=AgentRole.ANALYST,
        model="test-model",
    )

    with pytest.raises(ValueError, match="Critic"):
        build_critic_agent(config)


def test_critic_instructions_cover_validation_procedure() -> None:
    agent = build_critic_agent(model="test-model")
    instructions = agent.instructions.lower()

    for term in (
        "numerical",
        "business definitions",
        "denominators",
        "joins",
        "causal",
        "data-quality",
        "contradictions",
        "recommendation",
        "pass",
        "revise",
        "delegate",
    ):
        assert term in instructions


def test_critic_candidate_round_trips_candidate_evidence() -> None:
    candidate = CriticCandidate(
        objective="Validate Meta CAC.",
        findings=[
            Finding(
                id="F001",
                statement="Meta CAC was $10.",
                metric="CAC",
                value=10.0,
                evidence_refs=["working/queries/Q001.sql"],
                confidence=ConfidenceLevel.HIGH,
            )
        ],
        recommendations=["Keep the current acquisition budget pending review."],
        artifacts=["working/queries/Q001.sql"],
        evidence_refs=["tool-Q001"],
    )

    restored = CriticCandidate.model_validate_json(candidate.model_dump_json())

    assert restored == candidate
    assert restored.recommendations
    assert restored.findings[0].evidence_refs == ["working/queries/Q001.sql"]


def _context(tmp_path: Path) -> AgentRunContext:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace("run-critic")
    ledger = AnalysisLedger(workspace, objective=CRITIC_OBJECTIVE)
    ledger.update_budget(RunBudget(max_specialist_invocations=1, max_critic_loops=1))
    return AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id="run-critic",
            agent_role=AgentRole.CRITIC,
            model="test-model",
        ),
    )


def test_critic_persists_validation_result_and_issues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    candidate = CriticCandidate(objective="Validate a candidate finding.")
    issue = ValidationIssue(
        id="V001",
        severity=ValidationSeverity.HIGH,
        message="The candidate uses all customers instead of newly acquired customers.",
        category="denominator",
        evidence_refs=["tool-Q001"],
        recommendation=(
            "Recompute CAC using customers acquired in the reporting period."
        ),
    )
    validation = ValidationResult(
        status=ValidationStatus.REVISE,
        issues=[issue],
        checked_finding_ids=["F001"],
        summary="The denominator must be corrected.",
    )

    async def fake_run(agent, prompt, *, context, **kwargs):  # noqa: ANN001
        assert agent.output_type is ValidationResult
        assert "CANDIDATE_ANALYSIS_JSON" in prompt
        assert candidate.objective in prompt
        assert context is not None
        return SimpleNamespace(final_output=validation)

    monkeypatch.setattr(Runner, "run", fake_run)

    returned = asyncio.run(run_critic(context, candidate))
    reloaded = AnalysisLedger(context.ledger.state_path)

    assert returned == validation
    assert reloaded.validation_results == [validation]
    assert reloaded.validation_issues == [issue]
    assert reloaded.budget.specialist_invocations == 1
    assert reloaded.budget.critic_loops == 1
