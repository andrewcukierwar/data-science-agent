"""Deterministic checks for the canonical acceptance evaluator boundary."""

import asyncio
from pathlib import Path

from evaluation.canonical import CanonicalAcceptanceError, evaluate_canonical_run
from orchestration.runner import AnalysisRunner
from schemas.audit import AuditResult
from schemas.lead import LeadResult
from schemas.validation import ValidationResult, ValidationStatus
from tools.workspace import WorkspaceManager


def test_canonical_acceptance_rejects_incomplete_persisted_runs(
    tmp_path: Path,
) -> None:
    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        return AuditResult(status="complete")

    async def fake_lead(context, objective, *, business_context, audit, agent):  # noqa: ANN001
        return LeadResult(objective=objective, answer="Not enough evidence.")

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        context.consume_budget("specialist_invocations")
        context.consume_budget("critic_loops")
        return ValidationResult(status=ValidationStatus.PASS)

    result = asyncio.run(
        AnalysisRunner(
            workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
            auditor_runner=fake_auditor,
            lead_runner=fake_lead,
            critic_runner=fake_critic,
        ).run("canonical-incomplete", "Why did profitability decline?")
    )

    try:
        evaluate_canonical_run(result)
    except CanonicalAcceptanceError as error:
        assert "investigation plan" in str(error)
    else:
        raise AssertionError("incomplete run unexpectedly passed acceptance")
