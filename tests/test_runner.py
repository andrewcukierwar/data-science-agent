"""Deterministic lifecycle tests for the application-level AnalysisRunner."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from agents import MaxTurnsExceeded
from agents.runtime import AgentRole
from orchestration.ledger import AnalysisLedger
from orchestration.runner import AnalysisRunner
from schemas.audit import AuditResult, AuditStatus
from schemas.findings import ConfidenceLevel, Finding
from schemas.lead import LeadResult
from schemas.run_state import ArtifactKind, Hypothesis, RunBudget, RunStatus
from schemas.validation import (
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
    ValidationStatus,
)
from tools.artifacts import ArtifactManager
from tools.workspace import WorkspaceManager


def _usage():  # noqa: ANN202
    return SimpleNamespace(
        requests=1,
        input_tokens=10,
        output_tokens=4,
        total_tokens=14,
        input_tokens_details=SimpleNamespace(cached_tokens=2),
        output_tokens_details=SimpleNamespace(reasoning_tokens=1),
    )


def _audit() -> AuditResult:
    return AuditResult(status=AuditStatus.COMPLETE)


def test_runner_enforces_audit_remediation_critic_and_report_lifecycle(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    lead_calls = 0
    critic_calls = 0
    audit = _audit()
    hypothesis = Hypothesis(
        id="H001",
        statement="Acquisition efficiency declined.",
    )

    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        assert context.agent_role is AgentRole.DATA_AUDITOR
        assert context.ledger.state.status is RunStatus.RUNNING
        events.append("audit")
        context.record_sdk_usage(_usage())
        return audit

    async def fake_lead(context, objective, *, business_context, audit, agent):  # noqa: ANN001
        nonlocal lead_calls
        lead_calls += 1
        events.append("lead")
        assert context.agent_role is AgentRole.LEAD
        assert context.run_config.turn_limit == 16
        assert audit.status is AuditStatus.COMPLETE
        if lead_calls == 1:
            context.ledger.update_investigation_plan(
                ["Read definitions", "Test the primary profitability drivers"]
            )
            context.ledger.upsert_hypothesis(hypothesis)
        else:
            assert "CRITIC_VALIDATION_JSON" in objective
        context.record_sdk_usage(_usage())
        return LeadResult(
            objective="Explain profitability.",
            answer="Acquisition efficiency is the leading observed explanation.",
            hypotheses=[hypothesis],
            findings=[
                Finding(
                    id="F001",
                    statement="The data audit completed before analysis.",
                    evidence_refs=["audit-evidence"],
                    confidence=ConfidenceLevel.HIGH,
                )
            ],
        )

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        nonlocal critic_calls
        critic_calls += 1
        events.append("critic")
        assert context.agent_role is AgentRole.CRITIC
        assert context.run_config.turn_limit == 8
        context.consume_budget("critic_loops")
        context.record_sdk_usage(_usage())
        if critic_calls == 1:
            return ValidationResult(
                status=ValidationStatus.REVISE,
                issues=[
                    ValidationIssue(
                        id="V001",
                        severity=ValidationSeverity.MEDIUM,
                        message="Add evidence for the proposed recommendation.",
                    )
                ],
                summary="The candidate requires one bounded remediation.",
            )
        return ValidationResult(
            status=ValidationStatus.PASS,
            summary="The remediated candidate is reproducible.",
        )

    runner = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        model="test-model",
        model_provider="test-provider",
        budget=RunBudget(max_specialist_invocations=0, max_critic_loops=2),
        input_cost_per_1k_tokens=1.0,
        output_cost_per_1k_tokens=2.0,
        auditor_runner=fake_auditor,
        lead_runner=fake_lead,
        critic_runner=fake_critic,
    )
    result = asyncio.run(
        runner.run(
            "run-lifecycle",
            "Explain profitability.",
            business_context="Use reporting contribution profit.",
        )
    )

    assert result.status is RunStatus.COMPLETED
    assert result.constrained is False
    assert events == ["audit", "lead", "critic", "lead", "critic"]
    assert lead_calls == 2
    assert critic_calls == 2
    assert result.report is not None
    assert result.report.path == "outputs/report.md"
    assert result.workspace is not None
    assert (result.workspace.outputs / "report.md").exists()
    assert result.ledger is not None
    assert result.ledger.state.status is RunStatus.COMPLETED
    assert result.ledger.audit == audit
    assert result.ledger.state.investigation_plan == [
        "Read definitions",
        "Test the primary profitability drivers",
    ]
    assert result.ledger.hypotheses[0].id == "H001"
    assert result.ledger.hypothesis_history[0].id == "H001"
    assert len(result.ledger.hypothesis_history) == 1
    assert result.ledger.findings[0].id == "F001"
    assert result.ledger.state.final_report == result.report
    assert len(result.ledger.validation_results) == 2
    assert result.ledger.validation_issues[0].id == "V001"
    assert result.ledger.state.model == "test-model"
    assert result.ledger.state.model_provider == "test-provider"
    assert result.ledger.usage.requests == 5
    assert result.ledger.usage.total_tokens == 70
    assert result.ledger.state.elapsed_seconds is not None
    assert result.ledger.state.cost_estimation_note is not None
    report_text = (result.workspace.outputs / "report.md").read_text(encoding="utf-8")
    assert "The remediated candidate is reproducible." in report_text
    assert "Estimated model cost (USD): **0.090000**" in report_text
    assert "- Elapsed seconds: **" in report_text


def test_runner_mandatory_audit_does_not_consume_analytical_specialist_budget(
    tmp_path: Path,
) -> None:
    events: list[str] = []

    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        events.append("audit")
        return _audit()

    async def fake_lead(context, objective, *, business_context, audit, agent):  # noqa: ANN001
        events.append("lead")
        return LeadResult(objective=objective, answer="The audit is sufficient.")

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        events.append("critic")
        context.consume_budget("critic_loops")
        return ValidationResult(status=ValidationStatus.PASS)

    runner = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        budget=RunBudget(max_specialist_invocations=0, max_critic_loops=1),
        auditor_runner=fake_auditor,
        lead_runner=fake_lead,
        critic_runner=fake_critic,
    )
    result = asyncio.run(runner.run("run-audit-budget", "Explain profitability."))

    assert result.status is RunStatus.COMPLETED
    assert events == ["audit", "lead", "critic"]
    assert result.ledger is not None
    assert result.ledger.audit is not None
    assert result.ledger.audit.status is AuditStatus.COMPLETE
    assert result.ledger.budget.specialist_invocations == 0
    assert result.ledger.budget.critic_loops == 1


def test_report_lists_lead_chart_artifacts_in_supporting_visualizations(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace("run-report")
    ledger = AnalysisLedger(workspace, objective="Explain the change.")
    chart_path = workspace.outputs / "driver.png"
    chart_path.write_bytes(b"chart")
    chart = ArtifactManager(workspace, ledger).register(
        "outputs/driver.png",
        artifact_id="driver-chart",
        kind=ArtifactKind.CHART,
        media_type="image/png",
        description="Profitability driver comparison.",
    )
    report = AnalysisRunner._render_report(
        "Explain the change.",
        _audit(),
        LeadResult(
            objective="Explain the change.",
            answer="The answer is supported.",
            artifacts=[chart.id],
        ),
        ValidationResult(status=ValidationStatus.PASS),
        constrained=False,
        constraint_reason=None,
        ledger=ledger,
    )

    assert "## Supporting Visualizations" in report
    assert "[driver-chart](outputs/driver.png)" in report
    assert "Profitability driver comparison." in report


def test_runner_must_complete_objective_critical_lead_follow_up_before_critic(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    lead_calls = 0
    critic_calls = 0

    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        events.append("audit")
        return _audit()

    async def fake_lead(
        context,
        objective,
        *,
        business_context,
        audit,
        agent,
    ):  # noqa: ANN001
        nonlocal lead_calls
        lead_calls += 1
        events.append("lead")
        assert context.agent_role is AgentRole.LEAD
        if lead_calls == 1:
            return LeadResult(
                objective="Explain the KPI change.",
                answer=(
                    "A major component changed, but the upstream mechanism is "
                    "unresolved."
                ),
                open_questions=["Compare the available upstream funnel periods."],
                follow_up_analysis=True,
                follow_up_rationale=(
                    "The upstream funnel is material to answering why and is available."
                ),
            )
        assert "FOLLOW_UP_CYCLE: 1/2" in objective
        assert "Compare the available upstream funnel periods." in objective
        assert "PREVIOUS_LEAD_RESULT_JSON" in objective
        return LeadResult(
            objective="Explain the KPI change.",
            answer=(
                "The upstream mechanism was investigated with the available evidence."
            ),
            open_questions=[],
            follow_up_analysis=False,
        )

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        nonlocal critic_calls
        critic_calls += 1
        events.append("critic")
        assert candidate.follow_up_analysis is False
        assert candidate.follow_up_rationale is None
        assert "upstream mechanism" in candidate.answer
        context.consume_budget("critic_loops")
        return ValidationResult(
            status=ValidationStatus.PASS,
            summary="The required follow-up was completed before validation.",
        )

    runner = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        auditor_runner=fake_auditor,
        lead_runner=fake_lead,
        critic_runner=fake_critic,
    )
    result = asyncio.run(runner.run("run-follow-up", "Explain the KPI change."))

    assert result.status is RunStatus.COMPLETED
    assert result.constrained is False
    assert events == ["audit", "lead", "lead", "critic"]
    assert lead_calls == 2
    assert critic_calls == 1
    assert result.lead_result is not None
    assert result.lead_result.follow_up_analysis is False


def test_runner_allows_nonblocking_open_question_to_finalize(
    tmp_path: Path,
) -> None:
    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        return _audit()

    async def fake_lead(
        context,
        objective,
        *,
        business_context,
        audit,
        agent,
    ):  # noqa: ANN001
        return LeadResult(
            objective="Explain the KPI change.",
            answer=(
                "The available evidence supports the answer for the stated objective."
            ),
            open_questions=["A lower-priority experiment may be useful later."],
            follow_up_analysis=False,
        )

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        assert candidate.open_questions == [
            "A lower-priority experiment may be useful later."
        ]
        assert candidate.follow_up_analysis is False
        context.consume_budget("critic_loops")
        return ValidationResult(
            status=ValidationStatus.PASS,
            summary="The non-blocking question does not prevent completion.",
        )

    runner = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        auditor_runner=fake_auditor,
        lead_runner=fake_lead,
        critic_runner=fake_critic,
    )
    result = asyncio.run(
        runner.run("run-nonblocking-question", "Explain the KPI change.")
    )

    assert result.status is RunStatus.COMPLETED
    assert result.constrained is False
    assert result.ledger is not None
    assert result.ledger.state.open_questions == [
        "A lower-priority experiment may be useful later."
    ]


def test_runner_constrains_when_lead_follow_up_reaches_continuation_limit(
    tmp_path: Path,
) -> None:
    lead_calls = 0
    issue = ValidationIssue(
        id="V-FOLLOW-UP",
        severity=ValidationSeverity.HIGH,
        message="The candidate still needs its material follow-up.",
    )

    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        return _audit()

    async def persistent_follow_up_lead(
        context,
        objective,
        *,
        business_context,
        audit,
        agent,
    ):  # noqa: ANN001
        nonlocal lead_calls
        lead_calls += 1
        return LeadResult(
            objective="Explain the KPI change.",
            answer="The candidate remains incomplete.",
            open_questions=["The material upstream mechanism is unresolved."],
            follow_up_analysis=True,
            follow_up_rationale=(
                "The unresolved mechanism is answerable with available data."
            ),
        )

    async def bounded_critic(context, candidate, *, agent):  # noqa: ANN001
        assert candidate.follow_up_analysis is True
        context.consume_budget("critic_loops")
        return ValidationResult(status=ValidationStatus.REVISE, issues=[issue])

    runner = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        budget=RunBudget(max_critic_loops=1),
        auditor_runner=fake_auditor,
        lead_runner=persistent_follow_up_lead,
        critic_runner=bounded_critic,
    )
    result = asyncio.run(runner.run("run-follow-up-limit", "Explain the KPI change."))

    assert lead_calls == 3  # initial result plus the two allowed continuations
    assert result.status is RunStatus.BLOCKED
    assert result.constrained is True
    assert result.validation_result is not None
    assert result.lead_result is not None
    assert result.lead_result.follow_up_analysis is True
    report_text = (result.workspace.outputs / "report.md").read_text(encoding="utf-8")
    assert "maximum of 1 critic loop" in report_text
    assert "V-FOLLOW-UP" in report_text


def test_runner_returns_constrained_report_after_critic_limit(
    tmp_path: Path,
) -> None:
    issue = ValidationIssue(
        id="V001",
        severity=ValidationSeverity.HIGH,
        message="The recommendation is not supported by evidence.",
    )

    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        return _audit()

    async def fake_lead(context, objective, *, business_context, audit, agent):  # noqa: ANN001
        return LeadResult(
            objective="Explain profitability.",
            answer="The answer is provisional.",
            findings=[],
            recommendations=[],
            hypotheses=[],
            open_questions=[],
            artifacts=[],
            follow_up_analysis=False,
            follow_up_rationale=None,
            caveats=[],
        )

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        context.consume_budget("critic_loops")
        return ValidationResult(status=ValidationStatus.REVISE, issues=[issue])

    runner = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        budget=RunBudget(max_critic_loops=1),
        auditor_runner=fake_auditor,
        lead_runner=fake_lead,
        critic_runner=fake_critic,
    )
    result = asyncio.run(runner.run("run-constrained", "Explain profitability."))

    assert result.status is RunStatus.BLOCKED
    assert result.constrained is True
    assert result.validation_result is not None
    assert result.validation_result.status is ValidationStatus.REVISE
    assert result.ledger is not None
    assert result.ledger.state.status is RunStatus.BLOCKED
    report_text = (result.workspace.outputs / "report.md").read_text(encoding="utf-8")
    assert "Constrained Analysis Report" in report_text
    assert "V001" in report_text
    assert "provisional" in report_text
    assert "Remediation stop:" in report_text


def test_runner_preserves_candidate_when_sql_budget_stops_remediation(
    tmp_path: Path,
) -> None:
    lead_calls = 0
    issue = ValidationIssue(
        id="V-SQL",
        severity=ValidationSeverity.HIGH,
        message="Reproduce the candidate's SQL evidence.",
    )

    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        return _audit()

    async def fake_lead(
        context,
        objective,
        *,
        business_context,
        audit,
        agent,
    ):  # noqa: ANN001
        nonlocal lead_calls
        lead_calls += 1
        if lead_calls == 2:
            context.sql_service.execute("SELECT 1", query_id="Q-REMEDIATION")
        return LeadResult(
            objective="Explain profitability.",
            answer="The initial candidate remains the best available explanation.",
        )

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        context.consume_budget("critic_loops")
        return ValidationResult(status=ValidationStatus.REVISE, issues=[issue])

    runner = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        budget=RunBudget(max_sql_executions=0, max_critic_loops=2),
        auditor_runner=fake_auditor,
        lead_runner=fake_lead,
        critic_runner=fake_critic,
    )
    result = asyncio.run(runner.run("run-sql-remediation", "Explain profitability."))

    assert result.status is RunStatus.BLOCKED
    assert result.constrained is True
    assert result.lead_result is not None
    assert result.lead_result.answer.startswith("The initial candidate")
    assert result.validation_result is not None
    assert result.validation_result.status is ValidationStatus.REVISE
    assert result.ledger is not None
    assert result.ledger.state.error is None
    assert result.ledger.budget.sql_executions == 0
    report_text = (result.workspace.outputs / "report.md").read_text(encoding="utf-8")
    assert "Remediation stopped by budget exhaustion" in report_text
    assert "sql_executions" in report_text
    assert "V-SQL" in report_text
    assert result.ledger.agent_events[-1].status.value == "failed"


def test_runner_constrains_when_analytical_specialist_budget_stops_remediation(
    tmp_path: Path,
) -> None:
    lead_calls = 0
    issue = ValidationIssue(
        id="V-ANALYTICAL-REMEDIATION",
        severity=ValidationSeverity.HIGH,
        message="The candidate needs analytical evidence.",
    )

    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        return _audit()

    async def fake_lead(
        context,
        objective,
        *,
        business_context,
        audit,
        agent,
    ):  # noqa: ANN001
        nonlocal lead_calls
        lead_calls += 1
        context.consume_budget("specialist_invocations")
        return LeadResult(
            objective="Explain profitability.",
            answer="The initial candidate remains the best available explanation.",
        )

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        context.consume_budget("critic_loops")
        return ValidationResult(status=ValidationStatus.REVISE, issues=[issue])

    runner = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        budget=RunBudget(max_specialist_invocations=1, max_critic_loops=2),
        auditor_runner=fake_auditor,
        lead_runner=fake_lead,
        critic_runner=fake_critic,
    )
    result = asyncio.run(
        runner.run("run-analytical-remediation", "Explain profitability.")
    )

    assert lead_calls == 2
    assert result.status is RunStatus.BLOCKED
    assert result.constrained is True
    assert result.error is None
    assert result.validation_result is not None
    assert result.validation_result.status is ValidationStatus.REVISE
    assert result.ledger is not None
    assert result.ledger.budget.specialist_invocations == 1
    report_text = (result.workspace.outputs / "report.md").read_text(encoding="utf-8")
    assert "Remediation stopped by budget exhaustion" in report_text
    assert "V-ANALYTICAL-REMEDIATION" in report_text


def test_runner_preserves_candidate_when_lead_turns_stop_remediation(
    tmp_path: Path,
) -> None:
    lead_calls = 0
    issue = ValidationIssue(
        id="V-TURNS",
        severity=ValidationSeverity.MEDIUM,
        message="The candidate needs a bounded follow-up.",
    )

    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        return _audit()

    async def fake_lead(
        context,
        objective,
        *,
        business_context,
        audit,
        agent,
    ):  # noqa: ANN001
        nonlocal lead_calls
        lead_calls += 1
        if lead_calls == 2:
            raise MaxTurnsExceeded("Lead exceeded its 16-turn limit during remediation")
        return LeadResult(
            objective="Explain profitability.",
            answer="Candidate answer retained with explicit validation caveats.",
        )

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        context.consume_budget("critic_loops")
        return ValidationResult(status=ValidationStatus.REVISE, issues=[issue])

    runner = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        budget=RunBudget(max_critic_loops=2),
        auditor_runner=fake_auditor,
        lead_runner=fake_lead,
        critic_runner=fake_critic,
    )
    result = asyncio.run(runner.run("run-turn-remediation", "Explain profitability."))

    assert result.status is RunStatus.BLOCKED
    assert result.constrained is True
    assert result.lead_result is not None
    assert result.validation_result is not None
    assert result.validation_result.status is ValidationStatus.REVISE
    assert result.ledger is not None
    assert result.ledger.state.error is None
    report_text = (result.workspace.outputs / "report.md").read_text(encoding="utf-8")
    assert "Remediation stopped by the Lead turn limit" in report_text
    assert "V-TURNS" in report_text


def test_runner_re_reviews_even_when_analytical_specialist_budget_is_exhausted(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    lead_calls = 0
    critic_calls = 0
    issue = ValidationIssue(
        id="V-ANALYTICAL-BUDGET",
        severity=ValidationSeverity.HIGH,
        message="The candidate needs one bounded remediation.",
    )

    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        events.append("audit")
        return _audit()

    async def fake_lead(
        context,
        objective,
        *,
        business_context,
        audit,
        agent,
    ):  # noqa: ANN001
        nonlocal lead_calls
        lead_calls += 1
        events.append("lead")
        if lead_calls == 1:
            context.consume_budget("specialist_invocations")
        return LeadResult(
            objective="Explain profitability.",
            answer="The candidate is retained after bounded remediation.",
        )

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        nonlocal critic_calls
        critic_calls += 1
        events.append("critic")
        context.consume_budget("critic_loops")
        if critic_calls == 1:
            return ValidationResult(
                status=ValidationStatus.REVISE,
                issues=[issue],
                summary="The first review found an unresolved issue.",
            )
        return ValidationResult(status=ValidationStatus.PASS)

    runner = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        budget=RunBudget(max_specialist_invocations=1, max_critic_loops=2),
        auditor_runner=fake_auditor,
        lead_runner=fake_lead,
        critic_runner=fake_critic,
    )
    result = asyncio.run(runner.run("run-critic-budget", "Explain profitability."))

    assert result.status is RunStatus.COMPLETED
    assert result.constrained is False
    assert result.error is None
    assert events == ["audit", "lead", "critic", "lead", "critic"]
    assert lead_calls == 2
    assert critic_calls == 2
    assert result.ledger is not None
    assert result.ledger.state.error is None
    assert result.ledger.budget.specialist_invocations == 1
    assert result.ledger.budget.critic_loops == 2


def test_runner_goes_directly_from_remediation_to_critic_review(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    lead_calls = 0
    critic_calls = 0
    issue = ValidationIssue(
        id="V-DIRECT-REVIEW",
        severity=ValidationSeverity.MEDIUM,
        message="Complete the targeted follow-up.",
    )

    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        events.append("audit")
        return _audit()

    async def fake_lead(
        context,
        objective,
        *,
        business_context,
        audit,
        agent,
    ):  # noqa: ANN001
        nonlocal lead_calls
        lead_calls += 1
        events.append("lead")
        if lead_calls == 1:
            return LeadResult(
                objective="Explain profitability.",
                answer="The main driver is identified.",
                follow_up_analysis=False,
            )
        return LeadResult(
            objective="Explain profitability.",
            answer="The targeted remediation was attempted.",
            open_questions=["The remaining question is for Critic review."],
            follow_up_analysis=True,
            follow_up_rationale=(
                "The remaining question is material but now needs validation."
            ),
        )

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        nonlocal critic_calls
        critic_calls += 1
        events.append("critic")
        context.consume_budget("critic_loops")
        if critic_calls == 1:
            return ValidationResult(status=ValidationStatus.REVISE, issues=[issue])
        assert candidate.follow_up_analysis is True
        return ValidationResult(status=ValidationStatus.PASS)

    runner = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        budget=RunBudget(max_critic_loops=2),
        auditor_runner=fake_auditor,
        lead_runner=fake_lead,
        critic_runner=fake_critic,
    )
    result = asyncio.run(runner.run("run-direct-review", "Explain profitability."))

    assert result.status is RunStatus.COMPLETED
    assert events == ["audit", "lead", "critic", "lead", "critic"]
    assert lead_calls == 2
    assert critic_calls == 2
    assert result.lead_result is not None
    assert result.lead_result.follow_up_analysis is True


def test_runner_observes_lead_turn_limit_failure_and_marks_run_failed(
    tmp_path: Path,
) -> None:
    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        return _audit()

    async def exhausted_lead(
        context,
        objective,
        *,
        business_context,
        audit,
        agent,
    ):  # noqa: ANN001
        assert context.run_config.turn_limit == 16
        raise MaxTurnsExceeded("Lead exceeded its 16-turn limit")

    runner = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        auditor_runner=fake_auditor,
        lead_runner=exhausted_lead,
    )

    result = asyncio.run(runner.run("run-turn-limit", "Explain profitability."))

    assert result.status is RunStatus.FAILED
    assert result.error is not None
    assert "MaxTurnsExceeded" in result.error
    assert "16-turn limit" in result.error
    assert result.ledger is not None
    assert result.ledger.state.status is RunStatus.FAILED
    assert result.ledger.state.error == result.error
    assert result.ledger.agent_events[-1].agent_role == AgentRole.LEAD.value
    assert result.ledger.agent_events[-1].status.value == "failed"


def test_runner_marks_failed_and_persists_error_state(tmp_path: Path) -> None:
    async def failing_auditor(context, objective, *, agent):  # noqa: ANN001
        raise RuntimeError("auditor service unavailable")

    runner = AnalysisRunner(
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        auditor_runner=failing_auditor,
    )
    result = asyncio.run(runner.run("run-failed", "Explain profitability."))

    assert result.status is RunStatus.FAILED
    assert result.error is not None
    assert "auditor service unavailable" in result.error
    assert result.ledger is not None
    reloaded = type(result.ledger)(result.ledger.state_path)
    assert reloaded.state.status is RunStatus.FAILED
    assert reloaded.state.error == result.error
    assert reloaded.state.elapsed_seconds is not None
