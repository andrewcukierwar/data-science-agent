"""Evaluator-only definitions for the V1 experiment scenarios."""

from scenarios.definitions.models import (
    GroundTruthMetric,
    InjectedCondition,
    ScenarioDefinition,
)
from schemas.metrics import MetricComparisonType, MetricDefinitionContext
from schemas.statistics import (
    CausalInterpretation,
    ConfidenceInterval,
    StatisticalConclusion,
    StatisticalExpectation,
)

_EXPERIMENT_CONTEXT = MetricDefinitionContext(
    population="randomized experiment participants",
    date_basis="assignment",
    observation_window="experiment enrollment",
    numerator="successful outcomes",
    denominator="assigned participants",
    definition_ref="binary_experiment_outcome",
)
_ASSUMPTIONS = (
    "independent observations",
    "binary outcome",
    "random assignment",
    "adequate sample size",
    "two-sided alpha=0.05",
)


def _metric(expected: float, tolerance: float = 0.002) -> GroundTruthMetric:
    return GroundTruthMetric(
        id="checkout-treatment-effect",
        description="Treatment minus control conversion-rate difference.",
        comparison="checkout_treatment_minus_control_conversion",
        metric_key="experiment_conversion_effect",
        dimensions={"experiment": "checkout-v1"},
        baseline_period="control participants",
        comparison_period="treatment participants",
        comparison_type=MetricComparisonType.ABSOLUTE_DIFFERENCE,
        value_unit="fraction",
        expected_relative_change=expected,
        tolerance=tolerance,
        definition_context=_EXPERIMENT_CONTEXT,
    )


def _expectation(
    *,
    conclusion: StatisticalConclusion,
    estimate: float,
    lower: float,
    upper: float,
    p_value: float,
    effect_size: float,
    estimate_tolerance: float = 0.002,
    interval_tolerance: float = 0.002,
    p_value_tolerance: float = 1e-5,
    effect_size_tolerance: float = 0.002,
) -> StatisticalExpectation:
    return StatisticalExpectation(
        metric_key="experiment_conversion_effect",
        dimensions={"experiment": "checkout-v1"},
        baseline_period="control participants",
        comparison_period="treatment participants",
        expected_conclusion=conclusion,
        expected_estimate=estimate,
        estimate_tolerance=estimate_tolerance,
        expected_confidence_interval=ConfidenceInterval(lower=lower, upper=upper),
        confidence_interval_tolerance=interval_tolerance,
        expected_p_value=p_value,
        p_value_tolerance=p_value_tolerance,
        expected_effect_size=effect_size,
        effect_size_tolerance=effect_size_tolerance,
        practical_significance_threshold=0.05,
        expected_practically_significant=(
            conclusion is StatisticalConclusion.SIGNIFICANT_AND_PRACTICAL
        ),
        required_assumptions=_ASSUMPTIONS,
        expected_causal_interpretation=CausalInterpretation.CAUSAL_EFFECT_SUPPORTED,
    )


MEANINGFUL_EXPERIMENT_SCENARIO = ScenarioDefinition(
    scenario_id="meaningful-ab-treatment-effect",
    name="Controlled experiment decision",
    user_question=(
        "Should the company roll out the tested customer experience based on "
        "the observed experiment results?"
    ),
    generation_config={
        "experiment": {
            "seed": 2026,
            "control_n": 2_000,
            "treatment_n": 2_000,
            "control_rate": 0.20,
            "treatment_rate": 0.30,
            "practical_threshold": 0.05,
        }
    },
    injected_conditions=(
        InjectedCondition(
            id="meaningful-treatment-effect",
            description="Treatment conversion probability is 0.10 above control.",
            affected_tables=("experiment_observations",),
            relative_change=0.10,
        ),
    ),
    expected_primary_driver=(
        "The randomized treatment produces a statistically significant and "
        "practically meaningful positive conversion effect."
    ),
    expected_secondary_findings=(
        "The two-sided confidence interval excludes zero.",
        "The effect exceeds the declared practical-significance threshold.",
        "The randomized design supports a cautious treatment-effect interpretation.",
    ),
    known_non_drivers=(
        "A p-value being the same thing as effect size.",
        "A confidence interval being omitted because the result is significant.",
        "Unmeasured causal claims beyond the randomized treatment assignment.",
    ),
    expected_data_quality_findings=(
        "Each participant has one assignment and one binary outcome.",
        "Control and treatment sample sizes are explicit and reproducible.",
    ),
    ground_truth=(_metric(0.10),),
    statistical_expectation=_expectation(
        conclusion=StatisticalConclusion.SIGNIFICANT_AND_PRACTICAL,
        estimate=0.10,
        lower=0.0733416077,
        upper=0.1266583923,
        p_value=1.9495516312e-13,
        effect_size=0.2319842627,
    ),
)


NO_EFFECT_EXPERIMENT_SCENARIO = ScenarioDefinition(
    scenario_id="no-effect-ab-experiment",
    name="Controlled experiment decision",
    user_question=(
        "Should the company roll out the tested customer experience based on "
        "the observed experiment results?"
    ),
    generation_config={
        "experiment": {
            "seed": 2026,
            "control_n": 2_000,
            "treatment_n": 2_000,
            "control_rate": 0.25,
            "treatment_rate": 0.25,
            "practical_threshold": 0.05,
        }
    },
    injected_conditions=(
        InjectedCondition(
            id="no-treatment-effect",
            description="Treatment and control conversion probabilities are equal.",
            affected_tables=("experiment_observations",),
            relative_change=0.0,
        ),
    ),
    expected_primary_driver=(
        "The experiment does not establish a statistically significant treatment "
        "effect, so a rollout claim is unsupported."
    ),
    expected_secondary_findings=(
        "The confidence interval includes zero.",
        "The observed effect is below the practical-significance threshold.",
        "The randomized design does not convert a null result into a causal claim.",
    ),
    known_non_drivers=(
        "A small observed difference being treated as a proven effect.",
        "Statistical significance inferred from the point estimate alone.",
        "A rollout recommendation without interval and assumption checks.",
    ),
    expected_data_quality_findings=(
        "Each participant has one assignment and one binary outcome.",
        "Control and treatment sample sizes are explicit and reproducible.",
    ),
    ground_truth=(_metric(0.0),),
    statistical_expectation=_expectation(
        conclusion=StatisticalConclusion.NOT_STATISTICALLY_SIGNIFICANT,
        estimate=0.0,
        lower=-0.0268379122,
        upper=0.0268379122,
        p_value=1.0,
        effect_size=0.0,
    ),
)


IMMATERIAL_EXPERIMENT_SCENARIO = ScenarioDefinition(
    scenario_id="significant-but-immaterial-ab-effect",
    name="Controlled experiment decision",
    user_question=(
        "Should the company roll out the tested customer experience based on "
        "the observed experiment results?"
    ),
    generation_config={
        "experiment": {
            "seed": 2026,
            "control_n": 25_000,
            "treatment_n": 25_000,
            "control_rate": 0.30,
            "treatment_rate": 0.32,
            "practical_threshold": 0.05,
        }
    },
    injected_conditions=(
        InjectedCondition(
            id="immaterial-treatment-effect",
            description="Treatment conversion probability is 0.02 above control.",
            affected_tables=("experiment_observations",),
            relative_change=0.02,
        ),
    ),
    expected_primary_driver=(
        "The experiment is statistically significant but the estimated treatment "
        "effect is below the practical-significance threshold."
    ),
    expected_secondary_findings=(
        (
            "The confidence interval excludes zero while remaining below the "
            "practical threshold."
        ),
        "Statistical significance and business significance are distinct decisions.",
        (
            "The randomized design supports the treatment interpretation but not "
            "an oversized claim."
        ),
    ),
    known_non_drivers=(
        "A very small p-value being sufficient for rollout.",
        "The sample size turning an immaterial effect into a material one.",
        "An unsupported claim about outcomes outside the experiment population.",
    ),
    expected_data_quality_findings=(
        "Each participant has one assignment and one binary outcome.",
        "Control and treatment sample sizes are explicit and reproducible.",
    ),
    ground_truth=(_metric(0.02),),
    statistical_expectation=_expectation(
        conclusion=StatisticalConclusion.SIGNIFICANT_BUT_IMMATERIAL,
        estimate=0.02,
        lower=0.0118941804,
        upper=0.0281058196,
        p_value=1.3251605406e-6,
        effect_size=0.0432489526,
    ),
)


EXPERIMENT_SCENARIOS = (
    MEANINGFUL_EXPERIMENT_SCENARIO,
    NO_EFFECT_EXPERIMENT_SCENARIO,
    IMMATERIAL_EXPERIMENT_SCENARIO,
)


__all__ = [
    "EXPERIMENT_SCENARIOS",
    "IMMATERIAL_EXPERIMENT_SCENARIO",
    "MEANINGFUL_EXPERIMENT_SCENARIO",
    "NO_EFFECT_EXPERIMENT_SCENARIO",
]
