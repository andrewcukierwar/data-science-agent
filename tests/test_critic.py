"""Deterministic construction, candidate, and persistence tests."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
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
from schemas.metrics import MetricComparison
from schemas.run_state import RunBudget, ToolEvent, ToolEventStatus
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
        "inspect_relations",
        "run_sql",
        "run_python",
        "inspect_evidence",
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
        "inspect_evidence",
        "pass",
        "revise",
        "delegate",
    ):
        assert term in instructions


def test_critic_candidate_round_trips_candidate_evidence() -> None:
    candidate = CriticCandidate(
        objective="Validate Meta CAC.",
        answer="Meta CAC increased and should be investigated.",
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
        open_questions=["Would a longer cohort window change the LTV conclusion?"],
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
    ledger.update_budget(RunBudget(max_specialist_invocations=0, max_critic_loops=1))
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
    candidate = CriticCandidate(
        objective="Validate a candidate finding.",
        answer="The candidate is ready for validation.",
    )
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
        assert kwargs["max_turns"] == 8
        return SimpleNamespace(final_output=validation)

    monkeypatch.setattr(Runner, "run", fake_run)

    returned = asyncio.run(run_critic(context, candidate))
    reloaded = AnalysisLedger(context.ledger.state_path)

    assert returned == validation
    assert reloaded.validation_results == [validation]
    assert reloaded.validation_issues == [issue]
    assert reloaded.budget.specialist_invocations == 0
    assert reloaded.budget.critic_loops == 1


def test_critic_deterministically_revises_candidate_with_unresolved_follow_up(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    candidate = CriticCandidate(
        objective="Explain why the KPI changed.",
        answer="The largest channel declined, but the cause remains unknown.",
        open_questions=["Determine whether the upstream funnel changed."],
        follow_up_analysis=True,
        follow_up_rationale=(
            "The upstream funnel is material to answering why and is available."
        ),
    )

    result = asyncio.run(run_critic(context, candidate))

    assert result.status is ValidationStatus.REVISE
    assert result.issues[0].category == "task_completeness"
    assert "upstream funnel" in result.issues[0].message
    assert context.ledger.validation_results == [result]


def test_critic_allows_complete_candidate_to_reach_model_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    candidate = CriticCandidate(
        objective="Explain why the KPI changed.",
        answer="The available evidence supports the observed mechanism.",
        open_questions=["A lower-priority experiment could be useful later."],
        follow_up_analysis=False,
    )
    validation = ValidationResult(
        status=ValidationStatus.PASS,
        summary="The candidate is complete and supported.",
    )

    async def fake_run(agent, prompt, *, context, **kwargs):  # noqa: ANN001
        assert "open_questions" in prompt
        return SimpleNamespace(final_output=validation)

    monkeypatch.setattr(Runner, "run", fake_run)

    returned = asyncio.run(run_critic(context, candidate))

    assert returned.status is ValidationStatus.PASS


def test_critic_revises_structured_metric_inconsistent_with_evidence(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    evidence = MetricComparison(
        metric_key="cac",
        dimensions={"channel": "Paid"},
        baseline_period="Q1 2025",
        comparison_period="Q2 2025",
        comparison_type="relative_change",
        value=0.2,
        unit="relative_change_fraction",
        evidence_refs=["tool-metric"],
    )
    context.ledger.append_tool_event(
        ToolEvent(
            id="tool-metric",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=timestamp,
            completed_at=timestamp,
            output={"metric_comparisons": [evidence.model_dump(mode="json")]},
        )
    )
    candidate = CriticCandidate(
        objective="Explain the profitability change.",
        answer="The structured comparison supports the answer.",
        metric_comparisons=[evidence.model_copy(update={"value": 0.3})],
    )

    result = asyncio.run(run_critic(context, candidate))

    assert result.status is ValidationStatus.REVISE
    assert result.issues[0].category == "structured_metric"
    assert "inconsistent" in result.issues[0].message


def test_critic_requires_cogs_or_margin_when_profitability_data_has_cogs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    pd.DataFrame(
        {
            "order_id": ["O1"],
            "customer_id": ["C1"],
            "order_date": ["2025-01-01"],
            "net_revenue": [100.0],
            "cogs": [40.0],
        }
    ).to_parquet(source / "orders.parquet", index=False)
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-critic-margin",
        inputs_source=source,
    )
    ledger = AnalysisLedger(workspace, objective="Why did profitability change?")
    context = AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id="run-critic-margin",
            agent_role=AgentRole.CRITIC,
            model="test-model",
        ),
    )
    candidate = CriticCandidate(
        objective="Why did profitability change?",
        answer="Marketing spend changed and explains the result.",
    )

    result = asyncio.run(run_critic(context, candidate))

    assert result.status is ValidationStatus.REVISE
    assert result.issues[0].id == "V-COMPLETENESS-MARGIN"
    assert "COGS" in result.issues[0].message
