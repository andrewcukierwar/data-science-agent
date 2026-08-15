"""Deterministic lifecycle tests for the application-level AnalysisRunner."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from agents.runtime import AgentRole
from orchestration.runner import AnalysisRunner
from schemas.audit import AuditResult, AuditStatus
from schemas.findings import ConfidenceLevel, Finding
from schemas.lead import LeadResult
from schemas.run_state import Hypothesis, RunBudget, RunStatus
from schemas.validation import (
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
    ValidationStatus,
)
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
        assert audit.status is AuditStatus.COMPLETE
        if lead_calls == 1:
            context.ledger.update_investigation_plan(
                ["Read definitions", "Test the primary profitability drivers"]
            )
            context.ledger.upsert_hypothesis(
                Hypothesis(
                    id="H001",
                    statement="Acquisition efficiency declined.",
                )
            )
        else:
            assert "CRITIC_VALIDATION_JSON" in objective
        context.record_sdk_usage(_usage())
        return LeadResult(
            objective="Explain profitability.",
            answer="Acquisition efficiency is the leading observed explanation.",
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
        context.consume_budget("specialist_invocations")
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
        budget=RunBudget(max_critic_loops=2),
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
    assert result.ledger.findings[0].id == "F001"
    assert result.ledger.state.final_report == result.report
    assert len(result.ledger.validation_results) == 2
    assert result.ledger.validation_issues[0].id == "V001"
    assert result.ledger.state.model == "test-model"
    assert result.ledger.state.model_provider == "test-provider"
    assert result.ledger.usage.requests == 5
    assert result.ledger.usage.total_tokens == 70
    assert result.ledger.state.elapsed_seconds is not None
    assert "The remediated candidate is reproducible." in (
        result.workspace.outputs / "report.md"
    ).read_text(encoding="utf-8")


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
        context.consume_budget("specialist_invocations")
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
