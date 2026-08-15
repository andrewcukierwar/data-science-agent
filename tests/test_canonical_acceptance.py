"""Deterministic checks for the canonical acceptance evaluator boundary."""

import asyncio
from pathlib import Path

from evaluation.canonical import (
    CanonicalAcceptanceError,
    _canonical_numeric_ground_truth_failures,
    evaluate_canonical_run,
)
from orchestration.runner import AnalysisRunner
from scenarios.definitions import CANONICAL_PROFITABILITY_SCENARIO
from schemas.audit import AuditResult
from schemas.findings import ConfidenceLevel, Finding
from schemas.lead import LeadResult
from schemas.validation import ValidationResult, ValidationStatus
from tools.workspace import WorkspaceManager


def _canonical_findings(**overrides: float) -> list[Finding]:
    findings: list[Finding] = []
    for metric in CANONICAL_PROFITABILITY_SCENARIO.ground_truth:
        findings.append(
            Finding(
                id=f"F-{metric.id}",
                statement=f"Observed {metric.id}.",
                metric=metric.id.replace("-", "_"),
                value=overrides.get(metric.id, metric.expected_relative_change),
                value_unit=metric.value_unit,
                evidence_refs=["tool-evidence"],
                confidence=ConfidenceLevel.HIGH,
            )
        )
    return findings


def test_canonical_numeric_ground_truth_accepts_structured_values() -> None:
    assert _canonical_numeric_ground_truth_failures(_canonical_findings()) == []


def test_canonical_numeric_ground_truth_rejects_wrong_cac() -> None:
    failures = _canonical_numeric_ground_truth_failures(
        _canonical_findings(**{"meta-q2-cac": 0.90})
    )

    assert any("meta-q2-cac" in failure for failure in failures)
    assert any("outside" in failure for failure in failures)


def test_canonical_numeric_ground_truth_requires_the_declared_unit() -> None:
    findings = _canonical_findings()
    findings[0] = findings[0].model_copy(update={"value_unit": "percent"})

    failures = _canonical_numeric_ground_truth_failures(findings)

    assert any("relative_change_fraction" in failure for failure in failures)


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
