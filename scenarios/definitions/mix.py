"""Evaluator-only definition for the channel-mix confounding scenario."""

from scenarios.definitions.models import (
    GroundTruthMetric,
    InjectedCondition,
    ScenarioDefinition,
)
from schemas.metrics import MetricComparisonType, MetricDefinitionContext

_MIX_CONTEXT = MetricDefinitionContext(
    population="acquired customer cohort",
    date_basis="acquisition_date",
    observation_window="calendar quarter",
    numerator="acquired customers attributed to the channel",
    denominator="all acquired customers",
    definition_ref="acquired_customer_channel_share",
)
_COUNT_CONTEXT = MetricDefinitionContext(
    population="acquired customer cohort",
    date_basis="acquisition_date",
    observation_window="calendar quarter",
    numerator="acquired customers",
    denominator=None,
    definition_ref="acquired_customer_count",
)


CHANNEL_MIX_CONFOUNDING_SCENARIO = ScenarioDefinition(
    scenario_id="channel-mix-confounding",
    name="Acquisition channel mix analysis",
    user_question=(
        "Did a channel change cause the latest acquisition performance change, "
        "and what should be investigated?"
    ),
    generation_config={
        "baseline": {"seed": 42, "start_date": "2025-01-01"},
        "injection": {
            "source_channel": "Meta",
            "destination_channel": "Organic",
            "target_quarter": 2,
            "mix_fraction": 0.30,
        },
    },
    injected_conditions=(
        InjectedCondition(
            id="q2-acquisition-channel-mix-shift",
            description=(
                "A deterministic portion of Q2 acquired customers is attributed "
                "from one acquisition channel to another while total cohort volume "
                "is preserved."
            ),
            affected_tables=("customers", "sessions"),
            relative_change=0.30,
        ),
    ),
    expected_primary_driver=(
        "The apparent channel deterioration is a Q2 acquisition-channel mix shift, "
        "not evidence that one channel caused a company-wide acquisition change; "
        "total acquired-customer volume remains stable."
    ),
    expected_secondary_findings=(
        "Meta share falls while Organic share rises in the acquired-customer mix.",
        "The denominator for channel share is all acquired customers in the quarter.",
        "A channel-level attribution change should not be stated as causal proof.",
    ),
    known_non_drivers=(
        "A company-wide decline in total acquired-customer volume.",
        "A causal claim that Meta conversion caused the mix movement.",
        "A missing-day, broken-key, or undocumented-null defect.",
    ),
    expected_data_quality_findings=(
        "Customer IDs referenced by orders and sessions remain valid.",
        (
            "Non-converting acquisition sessions intentionally have null customer_id "
            "because no customer was created; this is expected."
        ),
        "The generated source has no duplicate primary keys or missing reporting days.",
        "Business definitions document acquisition-channel share and its denominator.",
    ),
    ground_truth=(
        GroundTruthMetric(
            id="meta-q2-acquisition-share",
            description="Meta Q2 acquired-customer share difference versus Q1.",
            comparison="acquired_customer_share_meta_q2_minus_q1",
            metric_key="acquired_customer_share",
            dimensions={"channel": "Meta"},
            baseline_period="Q1 2025",
            comparison_period="Q2 2025",
            comparison_type=MetricComparisonType.ABSOLUTE_DIFFERENCE,
            value_unit="fraction",
            expected_relative_change=-0.08,
            tolerance=0.015,
            definition_context=_MIX_CONTEXT,
        ),
        GroundTruthMetric(
            id="organic-q2-acquisition-share",
            description="Organic Q2 acquired-customer share difference versus Q1.",
            comparison="acquired_customer_share_organic_q2_minus_q1",
            metric_key="acquired_customer_share",
            dimensions={"channel": "Organic"},
            baseline_period="Q1 2025",
            comparison_period="Q2 2025",
            comparison_type=MetricComparisonType.ABSOLUTE_DIFFERENCE,
            value_unit="fraction",
            expected_relative_change=0.08,
            tolerance=0.015,
            definition_context=_MIX_CONTEXT,
        ),
        GroundTruthMetric(
            id="total-q2-acquired-customers",
            description="Total Q2 acquired-customer volume versus Q1.",
            comparison="total_acquired_customers_q2_vs_q1",
            metric_key="acquired_customers",
            dimensions={},
            baseline_period="Q1 2025",
            comparison_period="Q2 2025",
            comparison_type=MetricComparisonType.RELATIVE_CHANGE,
            expected_relative_change=0.0,
            tolerance=0.02,
            definition_context=_COUNT_CONTEXT,
        ),
    ),
)


__all__ = ["CHANNEL_MIX_CONFOUNDING_SCENARIO"]
