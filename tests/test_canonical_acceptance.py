"""Deterministic checks for the canonical acceptance evaluator boundary."""

import asyncio
from pathlib import Path

from evaluation.canonical import (
    CanonicalAcceptanceError,
    _canonical_numeric_ground_truth_failures,
    _has_asserted_primary_driver,
    _has_primary_channel_contribution,
    evaluate_canonical_run,
)
from orchestration.runner import AnalysisRunner
from scenarios.definitions import CANONICAL_PROFITABILITY_SCENARIO
from schemas.audit import AuditResult
from schemas.findings import ConfidenceLevel, Finding
from schemas.lead import LeadResult
from schemas.metrics import MetricComparison
from schemas.validation import ValidationResult, ValidationStatus
from tools.workspace import WorkspaceManager


def _canonical_comparisons(**overrides: float) -> list[MetricComparison]:
    comparisons: list[MetricComparison] = []
    for metric in CANONICAL_PROFITABILITY_SCENARIO.ground_truth:
        comparisons.append(
            MetricComparison(
                metric_key=metric.metric_key,
                dimensions=metric.dimensions,
                baseline_period=metric.baseline_period,
                comparison_period=metric.comparison_period,
                comparison_type=metric.comparison_type,
                value=overrides.get(metric.id, metric.expected_relative_change),
                unit=metric.value_unit,
                evidence_refs=["tool-evidence"],
            )
        )
    return comparisons


def test_canonical_numeric_ground_truth_accepts_structured_values() -> None:
    result = LeadResult(
        objective="Explain the change.",
        answer="The answer is supported.",
        findings=[
            Finding(
                id="model-local-finding-17",
                statement="A model-generated local finding ID is acceptable.",
                evidence_refs=["tool-evidence"],
                confidence=ConfidenceLevel.HIGH,
            )
        ],
        metric_comparisons=_canonical_comparisons(),
    )

    assert _canonical_numeric_ground_truth_failures(result) == []


def test_canonical_numeric_ground_truth_rejects_wrong_cac() -> None:
    failures = _canonical_numeric_ground_truth_failures(
        _canonical_comparisons(**{"meta-q2-cac": 0.90})
    )

    assert any("meta-q2-cac" in failure for failure in failures)
    assert any("outside" in failure for failure in failures)


def test_canonical_numeric_ground_truth_requires_the_declared_unit() -> None:
    comparisons = _canonical_comparisons()
    comparisons[0] = comparisons[0].model_copy(update={"unit": "percent"})

    failures = _canonical_numeric_ground_truth_failures(comparisons)

    assert any(
        "missing numeric ground-truth finding" in failure for failure in failures
    )


def test_generic_metric_aliases_and_paraphrased_periods_match_identity() -> None:
    aliases = {
        "conversion_rate": "session conversion",
        "acquired_customers": "new customers",
        "marketing_spend": "spend",
        "cac": "customer acquisition cost",
        "ltv": "90-day LTV",
    }
    comparisons = [
        comparison.model_copy(
            update={
                "metric_key": aliases[comparison.metric_key],
                "dimensions": {"acquisition_channel": "META"},
                "baseline_period": "2025 Q1",
                "comparison_period": "Q2 2025",
            }
        )
        for comparison in _canonical_comparisons()
    ]

    assert _canonical_numeric_ground_truth_failures(comparisons) == []


def test_canonical_acceptance_requires_asserted_root_cause_not_speculation() -> None:
    speculative = (
        "Meta declined and conversion may be worth investigating as a possible "
        "explanation for the acquisition deterioration."
    )
    asserted = (
        "Meta was the largest profitability driver. Meta conversion fell and "
        "drove the acquisition deterioration."
    )

    assert not _has_asserted_primary_driver(speculative)
    assert _has_asserted_primary_driver(asserted)
    assert _has_primary_channel_contribution(asserted)


def test_evaluator_metadata_does_not_enter_lead_or_critic_prompt_text() -> None:
    from agents.critic import _candidate_prompt
    from agents.lead import LEAD_INSTRUCTIONS, _lead_input

    expected_ids = " ".join(
        metric.id for metric in CANONICAL_PROFITABILITY_SCENARIO.ground_truth
    )
    assert expected_ids.split()[0] not in LEAD_INSTRUCTIONS
    assert expected_ids.split()[-1] not in _candidate_prompt(
        LeadResult(
            objective="Explain the change.",
            answer="Use generic metric comparisons.",
            metric_comparisons=[],
        )
    )
    lead_prompt = _lead_input("Explain the change.")
    assert all(
        metric.id not in lead_prompt
        for metric in CANONICAL_PROFITABILITY_SCENARIO.ground_truth
    )
    assert all(
        str(metric.expected_relative_change) not in lead_prompt
        for metric in CANONICAL_PROFITABILITY_SCENARIO.ground_truth
        if metric.expected_relative_change != 0
    )


def test_canonical_acceptance_rejects_incomplete_persisted_runs(
    tmp_path: Path,
) -> None:
    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        return AuditResult(status="complete")

    async def fake_lead(context, objective, *, business_context, audit, agent):  # noqa: ANN001
        return LeadResult(objective=objective, answer="Not enough evidence.")

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
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
