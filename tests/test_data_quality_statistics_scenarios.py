"""Deterministic fixtures for Task 5 data-quality and experiment scenarios."""

from collections.abc import Callable

import pandas as pd
import pytest

from evaluation.primitives import (
    DataQualityPolicy,
    evaluate_data_quality,
    evaluate_statistics,
    numeric_ground_truth_failures,
)
from evaluation.rules import (
    immaterial_experiment_rules,
    meaningful_experiment_rules,
    missing_reporting_day_rules,
    no_effect_experiment_rules,
    partial_latest_day_rules,
)
from scenarios import get_scenario
from scenarios.experiment_scenarios import statistical_assessment_for_scenario
from scenarios.generator import SyntheticEcommerceConfig, SyntheticEcommerceGenerator
from schemas.audit import AuditResult, AuditStatus, DataQualityIssue, IssueSeverity
from schemas.findings import SpecialistResult
from schemas.run_state import AnalysisRunState, SpecialistResultRecord
from schemas.statistics import (
    CausalInterpretation,
    ConfidenceInterval,
)


def _ecommerce_scale() -> SyntheticEcommerceConfig:
    return SyntheticEcommerceConfig(
        seed=42,
        num_customers=1_000,
        num_orders=4_000,
        num_sessions=8_000,
        num_products=4,
        period_days=365,
    )


@pytest.mark.parametrize(
    "scenario_id",
    ("missing-reporting-day", "partial-latest-reporting-day"),
)
def test_data_quality_traps_are_observable_and_invariants_pass(
    scenario_id: str,
) -> None:
    registration = get_scenario(scenario_id, "1.0")
    generated = registration.generate_validated(_ecommerce_scale())

    assert registration.invariant_suite.validate(generated.dataset).passed
    assert (
        numeric_ground_truth_failures(
            registration.invariant_suite.metric_observer(generated.dataset),
            registration.evaluation_spec.ground_truth,
        )
        == []
    )

    spend = generated.dataset.marketing_spend
    dates = pd.to_datetime(spend["date"]).dt.date
    if scenario_id == "missing-reporting-day":
        assert dates.nunique() == 364
        assert len(spend) == 364 * 5
    else:
        assert dates.nunique() == 365
        latest = dates.max()
        assert spend.loc[dates.eq(latest), "channel"].nunique() == 4


def test_clean_ecommerce_baseline_has_no_reporting_coverage_defect() -> None:
    baseline = SyntheticEcommerceGenerator(_ecommerce_scale()).generate()
    spend = baseline.marketing_spend
    dates = pd.to_datetime(spend["date"]).dt.date

    assert dates.nunique() == 365
    assert spend.loc[dates.eq(dates.max()), "channel"].nunique() == 5


@pytest.mark.parametrize(
    ("scenario_id", "expected_issue", "wrong_issue", "rules"),
    (
        (
            "missing-reporting-day",
            "missing_reporting_day",
            "partial_latest_reporting_day",
            missing_reporting_day_rules,
        ),
        (
            "partial-latest-reporting-day",
            "partial_latest_reporting_day",
            "missing_reporting_day",
            partial_latest_day_rules,
        ),
    ),
)
def test_auditor_defect_recall_and_wrong_defect_rejection(
    scenario_id: str,
    expected_issue: str,
    wrong_issue: str,
    rules: Callable,
) -> None:
    policy = rules().data_quality_policy

    correct_state = _state(audit=_audit(expected_issue))
    correct_checks = evaluate_data_quality(correct_state, policy)
    assert all(check.status.value == "pass" for check in correct_checks)

    wrong_state = _state(audit=_audit(wrong_issue))
    wrong_checks = evaluate_data_quality(wrong_state, policy)
    assert any(check.status.value == "fail" for check in wrong_checks)


def test_clean_data_quality_policy_rejects_false_positive() -> None:
    policy = DataQualityPolicy(
        maximum_issue_severity=IssueSeverity.HIGH,
        forbid_any_issues=True,
    )
    clean_checks = evaluate_data_quality(
        _state(audit=AuditResult(status=AuditStatus.COMPLETE)),
        policy,
    )
    assert all(check.status.value == "pass" for check in clean_checks)

    false_positive_checks = evaluate_data_quality(
        _state(audit=_audit("missing_reporting_day")),
        policy,
    )
    assert any(
        check.check_id == "data_quality:no_issues" and check.status.value == "fail"
        for check in false_positive_checks
    )


@pytest.mark.parametrize(
    ("scenario_id", "expected_n", "control_rate", "treatment_rate"),
    (
        ("meaningful-ab-treatment-effect", 2_000, 0.20, 0.30),
        ("no-effect-ab-experiment", 2_000, 0.25, 0.25),
        ("significant-but-immaterial-ab-effect", 25_000, 0.30, 0.32),
    ),
)
def test_experiment_fixtures_have_stable_known_sampling_properties(
    scenario_id: str,
    expected_n: int,
    control_rate: float,
    treatment_rate: float,
) -> None:
    registration = get_scenario(scenario_id, "1.0")
    first = registration.generate_validated()
    second = registration.generate_validated()
    pd.testing.assert_frame_equal(
        first.dataset.observations,
        second.dataset.observations,
    )
    assert first.dataset.business_definitions == second.dataset.business_definitions

    observations = first.dataset.observations
    assert len(observations) == expected_n * 2
    assert observations.groupby("assignment")["outcome"].size().to_dict() == {
        "control": expected_n,
        "treatment": expected_n,
    }
    assert observations.groupby("assignment")["outcome"].mean().to_dict() == {
        "control": control_rate,
        "treatment": treatment_rate,
    }
    assessment = statistical_assessment_for_scenario(first.dataset, first.definition)
    assert (
        assessment.conclusion
        is first.definition.statistical_expectation.expected_conclusion
    )


@pytest.mark.parametrize(
    "scenario_id",
    (
        "meaningful-ab-treatment-effect",
        "no-effect-ab-experiment",
        "significant-but-immaterial-ab-effect",
    ),
)
def test_experiment_sources_and_model_context_do_not_reveal_conclusion(
    scenario_id: str,
) -> None:
    registration = get_scenario(scenario_id, "1.0")
    generated = registration.generate_validated()
    source_text = generated.dataset.business_definitions.lower()
    model_text = registration.model_context_contract().model_dump_json().lower()

    assert "statistically significant" not in source_text
    assert "immaterial" not in source_text
    assert "expected_conclusion" not in model_text
    assert "statistical_expectation" not in model_text
    assert "ground_truth" not in model_text


@pytest.mark.parametrize(
    ("scenario_id", "rules"),
    (
        ("meaningful-ab-treatment-effect", meaningful_experiment_rules),
        ("no-effect-ab-experiment", no_effect_experiment_rules),
        (
            "significant-but-immaterial-ab-effect",
            immaterial_experiment_rules,
        ),
    ),
)
def test_statistical_evaluator_checks_typed_assessment(
    scenario_id: str,
    rules: Callable,
) -> None:
    registration = get_scenario(scenario_id, "1.0")
    generated = registration.generate_validated()
    assessment = statistical_assessment_for_scenario(
        generated.dataset, generated.definition
    )
    state = _state(
        specialist_results=[
            SpecialistResultRecord(
                agent_role="statistician",
                result=SpecialistResult(
                    objective="Assess the experiment",
                    statistical_assessments=[assessment],
                ),
            )
        ]
    )
    conclusion = {
        "meaningful-ab-treatment-effect": (
            "The treatment is statistically significant and practical."
        ),
        "no-effect-ab-experiment": (
            "The treatment is not statistically significant and the interval "
            "includes zero."
        ),
        "significant-but-immaterial-ab-effect": (
            "The treatment is statistically significant but immaterial and not "
            "practical."
        ),
    }[scenario_id]
    report = (
        "The confidence interval and effect size support the practical "
        "significance conclusion after checking each assumption; the randomized "
        "design supports a cautious causal interpretation. " + conclusion
    )
    checks = evaluate_statistics(state, report, rules().statistics_policy)
    assert all(check.status.value == "pass" for check in checks), checks


@pytest.mark.parametrize(
    "field_update",
    (
        {"conclusion": "not_statistically_significant"},
        {"confidence_level": 0.90},
        {"confidence_interval": ConfidenceInterval(lower=0.5, upper=0.6)},
        {"p_value": 0.5},
        {"effect_size": 0.0},
        {"practical_significance_threshold": 0.01},
        {"practically_significant": False},
        {"assumptions_checked": ("binary outcome",)},
        {"causal_interpretation": CausalInterpretation.ASSOCIATION_ONLY},
    ),
)
def test_statistical_evaluator_rejects_incomplete_or_wrong_claims(
    field_update: dict[str, object],
) -> None:
    registration = get_scenario("meaningful-ab-treatment-effect", "1.0")
    generated = registration.generate_validated()
    assessment = statistical_assessment_for_scenario(
        generated.dataset, generated.definition
    )
    altered = assessment.model_copy(update=field_update)
    state = _state(
        specialist_results=[
            SpecialistResultRecord(
                agent_role="statistician",
                result=SpecialistResult(
                    objective="Assess the experiment",
                    statistical_assessments=[altered],
                ),
            )
        ]
    )
    checks = evaluate_statistics(
        state,
        "confidence interval effect size practical significance assumptions causal "
        "statistically significant and practical",
        meaningful_experiment_rules().statistics_policy,
    )
    assert any(check.status.value == "fail" for check in checks)


def _audit(*issue_ids: str) -> AuditResult:
    return AuditResult(
        status=AuditStatus.COMPLETE,
        issues=[
            DataQualityIssue(
                id=issue_id,
                severity=IssueSeverity.MEDIUM,
                message=f"Observed {issue_id}",
                evidence_refs=[f"evidence:{issue_id}"],
            )
            for issue_id in issue_ids
        ],
    )


def _state(**values: object) -> AnalysisRunState:
    return AnalysisRunState(
        run_id="task5-fixture",
        objective="Task 5 deterministic fixture",
        **values,
    )
