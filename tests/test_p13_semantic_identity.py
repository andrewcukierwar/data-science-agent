"""Version 1.3 evaluator identity and selected-statistic regressions."""

import pytest

from evaluation.primitives import (
    _statistical_assessment_matches,
    _statistical_assessments,
    evaluate_statistics,
    numeric_ground_truth_failures,
)
from scenarios import get_scenario
from scenarios.definitions.models import GroundTruthMetric
from scenarios.experiment_scenarios import statistical_assessment_for_scenario
from schemas.findings import SpecialistResult
from schemas.metrics import (
    MetricComparison,
    MetricComparisonType,
    MetricDefinitionContext,
)
from schemas.run_state import AnalysisRunState, SpecialistResultRecord


def _context(
    *,
    population: str = "randomized experiment participants",
    date_basis: str = "assignment",
    observation_window: str = "experiment enrollment",
    numerator: str = "successful outcomes",
    denominator: str = "assigned participants",
    definition_ref: str = "outcome-rate-difference",
) -> MetricDefinitionContext:
    return MetricDefinitionContext(
        population=population,
        date_basis=date_basis,
        observation_window=observation_window,
        numerator=numerator,
        denominator=denominator,
        definition_ref=definition_ref,
    )


def _expected_metric() -> GroundTruthMetric:
    return GroundTruthMetric(
        id="generic-effect-slot",
        description="Signed absolute outcome-rate difference.",
        comparison="treatment-minus-control",
        metric_key="rate_difference",
        dimensions={"acquisition_channel": "Campaign-North-7"},
        baseline_period="control participants",
        comparison_period="treatment participants",
        comparison_type=MetricComparisonType.ABSOLUTE_DIFFERENCE,
        value_unit="fraction",
        expected_relative_change=0.10,
        tolerance=0.002,
        definition_context=_context(),
    )


def _comparison(**updates) -> MetricComparison:
    values = {
        "metric_key": "binary_outcome_rate_difference",
        "dimensions": {"channel": "Campaign-North-7"},
        "baseline_period": "control arm",
        "comparison_period": "treatment participants",
        "comparison_type": MetricComparisonType.ABSOLUTE_DIFFERENCE,
        "value": 0.10,
        "unit": "proportion",
        "evidence_refs": ["executed-evidence"],
        "definition_context": _context(
            population="participants randomized to the experiment",
            date_basis="random assignment date",
            observation_window="experiment enrollment window",
            numerator="conversions",
            denominator="all randomly assigned participants",
            definition_ref="model-authored-label",
        ),
    }
    values.update(updates)
    return MetricComparison(**values)


def test_equivalent_structured_estimand_survives_generic_wording_changes() -> None:
    assert numeric_ground_truth_failures([_comparison()], [_expected_metric()]) == []


@pytest.mark.parametrize(
    "context_update",
    (
        {"population": "all website visitors"},
        {"observation_window": "30-day follow-up"},
        {"denominator": "eligible visitors"},
        {"numerator": "purchase revenue"},
    ),
)
def test_population_window_numerator_or_denominator_changes_split_estimand(
    context_update: dict[str, str],
) -> None:
    original = _comparison()
    changed_context = original.definition_context.model_copy(update=context_update)
    changed = original.model_copy(update={"definition_context": changed_context})

    failures = numeric_ground_truth_failures([changed], [_expected_metric()])
    assert any("missing numeric" in failure for failure in failures)


@pytest.mark.parametrize(
    "context_update",
    (
        {"population": "experiment participants who converted"},
        {"denominator": "randomly assigned participants with complete outcomes"},
        {"observation_window": "experiment enrollment and follow-up window"},
        {"date_basis": "assignment date and session date"},
    ),
)
def test_qualified_population_denominator_window_or_date_is_not_equivalent(
    context_update: dict[str, str],
) -> None:
    original = _comparison()
    changed = original.model_copy(
        update={
            "definition_context": original.definition_context.model_copy(
                update=context_update
            )
        }
    )
    assert numeric_ground_truth_failures([changed], [_expected_metric()])


def test_unrelated_rate_difference_is_not_conversion_effect() -> None:
    changed = _comparison(metric_key="click_rate_difference")
    assert numeric_ground_truth_failures([changed], [_expected_metric()])


def test_dimension_date_and_treatment_control_orientation_remain_distinct() -> None:
    expected = _expected_metric().model_copy(
        update={
            "baseline_period": "Q4 2032",
            "comparison_period": "Q1 2033",
        }
    )
    equivalent = _comparison(
        dimensions={"channel_name": "Campaign-North-7"},
        baseline_period="2032 Q4",
        comparison_period="Q1-2033",
    )
    assert numeric_ground_truth_failures([equivalent], [expected]) == []

    wrong_dimension = equivalent.model_copy(
        update={"dimensions": {"channel_name": "Campaign-South-4"}}
    )
    wrong_date = equivalent.model_copy(update={"comparison_period": "Q2 2033"})
    reversed_arms = equivalent.model_copy(
        update={
            "baseline_period": "treatment arm",
            "comparison_period": "control arm",
        }
    )
    for changed in (wrong_dimension, wrong_date, reversed_arms):
        assert numeric_ground_truth_failures([changed], [expected])


def test_frozen_v12_metric_contract_keeps_its_original_superset_rule() -> None:
    expected = _expected_metric().model_copy(
        update={
            "metric_key": "rate_difference",
            "dimensions": {"channel": "Campaign-North-7"},
        }
    )
    historical_superset = _comparison(
        metric_key="rate_difference",
        dimensions={"channel": "Campaign-North-7", "cohort": "assigned participants"},
        baseline_period="control participants",
        comparison_period="treatment participants",
        unit="fraction",
        definition_context=expected.definition_context,
    )

    assert (
        numeric_ground_truth_failures(
            [historical_superset], [expected], legacy_contract=True
        )
        == []
    )
    assert numeric_ground_truth_failures(
        [historical_superset], [expected], legacy_contract=False
    )


def _current_statistical_fixture():
    registration = get_scenario("meaningful-ab-treatment-effect", "1.1")
    generated = registration.generate_validated()
    assessment = statistical_assessment_for_scenario(
        generated.dataset, generated.definition
    )
    return registration, assessment


def test_legacy_statistical_metric_keys_keep_historical_identity() -> None:
    registration, assessment = _current_statistical_fixture()
    expectation = registration.evaluation_spec.statistical_expectation.model_copy(
        update={"metric_key": "conversion_rate_difference"}
    )
    unrelated = assessment.model_copy(update={"metric_key": "click_rate_difference"})
    assert not _statistical_assessment_matches(
        unrelated, expectation, legacy_contract=True
    )


def _statistical_report() -> str:
    return (
        "The confidence interval, effect size, practical significance and "
        "assumptions support the randomized causal interpretation. The result is "
        "statistically significant and practical."
    )


def test_experiment_identity_accepts_equivalent_context_and_assumption_wording() -> (
    None
):
    registration, assessment = _current_statistical_fixture()
    equivalent = assessment.model_copy(
        update={
            "metric_key": "binary_outcome_rate_difference",
            "baseline_period": "control arm",
            "comparison_period": "treatment group",
            "definition_context": _context(
                population="participants randomized to the experiment",
                date_basis="random assignment date",
                observation_window="experiment enrollment window",
                numerator="conversions",
                denominator="all randomly assigned participants",
                definition_ref="alternate-display-label",
            ),
            "assumptions_checked": (
                "independent subjects",
                "binary response",
                "randomized allocation",
                "sufficient sample size",
                "two-tailed test at alpha 5%",
            ),
        }
    )
    state = AnalysisRunState(
        schema_version="1.3",
        run_id="selected-statistic",
        objective="Compare the assigned experiment arms.",
        statistical_assessments=[equivalent],
    )

    checks = evaluate_statistics(
        state, _statistical_report(), registration.evaluator_rules().statistics_policy
    )
    assert all(item.status.value == "pass" for item in checks), checks


@pytest.mark.parametrize(
    "field_update",
    (
        {"procedure": None},
        {"effect_size_method": None},
        {
            "baseline_period": "treatment participants",
            "comparison_period": "control participants",
        },
        {
            "definition_context": _context(denominator="session attendees"),
        },
    ),
)
def test_required_method_reversal_and_scope_mismatches_fail(
    field_update: dict[str, object],
) -> None:
    registration, assessment = _current_statistical_fixture()
    altered = assessment.model_copy(update=field_update)
    state = AnalysisRunState(
        schema_version="1.3",
        run_id="wrong-statistical-identity",
        objective="Compare the assigned experiment arms.",
        statistical_assessments=[altered],
    )
    checks = evaluate_statistics(
        state, _statistical_report(), registration.evaluator_rules().statistics_policy
    )
    assert any(item.status.value == "fail" for item in checks)


def test_selected_statistical_record_ignores_history_but_conflicts_still_fail() -> None:
    registration, selected = _current_statistical_fixture()
    superseded = selected.model_copy(update={"estimate": -0.40})
    stale_specialist = SpecialistResultRecord(
        agent_role="statistician",
        result=SpecialistResult(
            objective="Historical computation",
            statistical_assessments=[superseded],
        ),
    )
    state = AnalysisRunState(
        schema_version="1.3",
        run_id="selected-statistical-history",
        objective="Compare the assigned experiment arms.",
        statistical_assessments=[selected],
        statistical_assessment_history=[superseded],
        specialist_results=[stale_specialist],
    )
    assert _statistical_assessments(state) == (selected,)
    selected_checks = evaluate_statistics(
        state, _statistical_report(), registration.evaluator_rules().statistics_policy
    )
    assert all(item.status.value == "pass" for item in selected_checks)

    conflict = selected.model_copy(update={"estimate": -0.40})
    unresolved = state.model_copy(
        update={"statistical_assessments": [selected, conflict]}
    )
    conflict_checks = evaluate_statistics(
        unresolved,
        _statistical_report(),
        registration.evaluator_rules().statistics_policy,
    )
    assert any(
        item.check_id.endswith(":present") and item.status.value == "fail"
        for item in conflict_checks
    )
