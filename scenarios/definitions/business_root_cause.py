"""Evaluator-only definitions for the Phase 2 business root-cause scenarios."""

from scenarios.definitions.models import (
    GroundTruthMetric,
    InjectedCondition,
    ScenarioDefinition,
)
from schemas.metrics import MetricComparisonType, MetricDefinitionContext

_COMMON_DATA_QUALITY_FINDINGS = (
    "Customer IDs referenced by orders and sessions remain valid.",
    (
        "Non-converting acquisition sessions intentionally have null customer_id "
        "because no customer was created; this is expected."
    ),
    "The generated source has no duplicate primary keys or missing reporting days.",
    "Business definitions document the reporting metrics and economic identities.",
)


def _cohort_context(
    *,
    numerator: str,
    denominator: str | None,
    definition_ref: str,
) -> MetricDefinitionContext:
    return MetricDefinitionContext(
        population="acquired customer cohort",
        date_basis="acquisition_date cohort and order_date observation",
        observation_window="90_day",
        numerator=numerator,
        denominator=denominator,
        definition_ref=definition_ref,
    )


RETENTION_DETERIORATION_SCENARIO = ScenarioDefinition(
    scenario_id="retention-q2-deterioration",
    name="Customer cohort profitability analysis",
    user_question=(
        "Why did profitability change across customer cohorts, and what should "
        "the company do about it?"
    ),
    generation_config={
        "baseline": {"seed": 42, "start_date": "2025-01-01"},
        "injection": {
            "channel": "Email",
            "target_quarter": 2,
            "retention_multiplier": 0.70,
        },
    },
    injected_conditions=(
        InjectedCondition(
            id="email-q2-repeat-purchase-change",
            description=(
                "For one acquisition channel, the Q2 cohort has fewer customers "
                "with a second order inside the declared 90-day observation window."
            ),
            affected_tables=("customers", "orders"),
            relative_change=-0.30,
        ),
    ),
    expected_primary_driver=(
        "Email Q2 acquired-customer retention deteriorated: fewer customers made "
        "a second order within 90 days, reducing cohort contribution profit."
    ),
    expected_secondary_findings=(
        "Email acquired-customer volume and CAC remain approximately stable.",
        "The retention comparison uses acquisition cohorts and a 90-day window.",
        "The clean source has no injected key, date, or null-quality defect.",
    ),
    known_non_drivers=(
        "Email acquisition volume or CAC materially changing.",
        "A broad COGS or margin shock.",
        "A missing-day, broken-key, or undocumented-null defect.",
    ),
    expected_data_quality_findings=_COMMON_DATA_QUALITY_FINDINGS,
    ground_truth=(
        GroundTruthMetric(
            id="email-q2-retention-rate",
            description="Email Q2 repeat-purchase rate versus Email Q1.",
            comparison="retention_email_q2_vs_q1_90_day",
            metric_key="retention_rate",
            dimensions={"channel": "Email"},
            baseline_period="Q1 2025",
            comparison_period="Q2 2025",
            comparison_type=MetricComparisonType.RELATIVE_CHANGE,
            value_unit="relative_change_fraction",
            expected_relative_change=-0.30,
            tolerance=0.08,
            definition_context=_cohort_context(
                numerator="retained customers with a second order",
                denominator="acquired customers",
                definition_ref="retention_rate_90_day",
            ),
        ),
        GroundTruthMetric(
            id="email-q2-acquired-customers",
            description="Email Q2 acquired-customer volume versus Email Q1.",
            comparison="acquired_email_q2_vs_q1",
            metric_key="acquired_customers",
            dimensions={"channel": "Email"},
            baseline_period="Q1 2025",
            comparison_period="Q2 2025",
            comparison_type=MetricComparisonType.RELATIVE_CHANGE,
            expected_relative_change=0.0,
            tolerance=0.08,
            definition_context=_cohort_context(
                numerator="acquired customers",
                denominator=None,
                definition_ref="acquired_customer_count",
            ),
        ),
        GroundTruthMetric(
            id="email-q2-cac",
            description="Email Q2 CAC versus Email Q1.",
            comparison="cac_email_q2_vs_q1",
            metric_key="cac",
            dimensions={"channel": "Email"},
            baseline_period="Q1 2025",
            comparison_period="Q2 2025",
            comparison_type=MetricComparisonType.RELATIVE_CHANGE,
            expected_relative_change=0.0,
            tolerance=0.12,
            definition_context=_cohort_context(
                numerator="marketing spend",
                denominator="acquired customers",
                definition_ref="cac_same_period_channel",
            ),
        ),
    ),
)


COGS_MARGIN_DETERIORATION_SCENARIO = ScenarioDefinition(
    scenario_id="cogs-q2-margin-deterioration",
    name="Unit economics profitability analysis",
    user_question=(
        "Why did profitability change in the latest reporting period, and what "
        "should the company do about it?"
    ),
    generation_config={
        "baseline": {"seed": 42, "start_date": "2025-01-01"},
        "injection": {
            "channel": "Google",
            "target_quarter": 2,
            "margin_delta": 0.12,
        },
    },
    injected_conditions=(
        InjectedCondition(
            id="google-q2-unit-cost-change",
            description=(
                "For one acquisition channel, the Q2 cohort's COGS-to-net-revenue "
                "ratio is higher for orders in the declared 90-day window."
            ),
            affected_tables=("customers", "orders"),
            relative_change=0.12,
        ),
    ),
    expected_primary_driver=(
        "Google Q2 COGS rose relative to net revenue, reducing the cohort's "
        "contribution margin."
    ),
    expected_secondary_findings=(
        "Google acquired-customer volume and acquisition economics remain stable.",
        "Discount and refund rates are not the material explanation.",
        "The COGS comparison uses the acquired cohort's order-date 90-day window.",
    ),
    known_non_drivers=(
        "A material Google acquisition-volume or CAC change.",
        "A broad discount or refund shock.",
        "A missing-day, broken-key, or undocumented-null defect.",
    ),
    expected_data_quality_findings=_COMMON_DATA_QUALITY_FINDINGS,
    ground_truth=(
        GroundTruthMetric(
            id="google-q2-cogs-ratio",
            description=(
                "Google Q2 COGS-to-net-revenue ratio difference versus Google Q1."
            ),
            comparison="cogs_to_revenue_google_q2_minus_q1_90_day",
            metric_key="cogs_to_revenue_ratio",
            dimensions={"channel": "Google"},
            baseline_period="Q1 2025",
            comparison_period="Q2 2025",
            comparison_type=MetricComparisonType.ABSOLUTE_DIFFERENCE,
            value_unit="fraction",
            expected_relative_change=0.12,
            tolerance=0.02,
            definition_context=_cohort_context(
                numerator="cogs",
                denominator="net revenue",
                definition_ref="cogs_to_net_revenue_ratio_90_day",
            ),
        ),
        GroundTruthMetric(
            id="google-q2-acquired-customers",
            description="Google Q2 acquired-customer volume versus Google Q1.",
            comparison="acquired_google_q2_vs_q1",
            metric_key="acquired_customers",
            dimensions={"channel": "Google"},
            baseline_period="Q1 2025",
            comparison_period="Q2 2025",
            comparison_type=MetricComparisonType.RELATIVE_CHANGE,
            expected_relative_change=0.0,
            tolerance=0.08,
            definition_context=_cohort_context(
                numerator="acquired customers",
                denominator=None,
                definition_ref="acquired_customer_count",
            ),
        ),
    ),
)


DISCOUNT_REFUND_DETERIORATION_SCENARIO = ScenarioDefinition(
    scenario_id="discount-refund-q2-deterioration",
    name="Revenue realization profitability analysis",
    user_question=(
        "Why did profitability change in the latest reporting period, and what "
        "should the company do about it?"
    ),
    generation_config={
        "baseline": {"seed": 42, "start_date": "2025-01-01"},
        "injection": {
            "channel": "Affiliate",
            "target_quarter": 2,
            "discount_rate_delta": 0.05,
            "refund_rate_delta": 0.04,
        },
    },
    injected_conditions=(
        InjectedCondition(
            id="affiliate-q2-revenue-realization-change",
            description=(
                "For one acquisition channel, Q2 cohort discount and refund rates "
                "are higher for orders in the declared 90-day window."
            ),
            affected_tables=("customers", "orders"),
            relative_change=0.09,
        ),
    ),
    expected_primary_driver=(
        "Affiliate Q2 discounts and refunds increased, reducing realized net "
        "revenue and contribution margin."
    ),
    expected_secondary_findings=(
        "Affiliate acquired-customer volume remains approximately stable.",
        "Gross order demand and COGS-to-gross-revenue economics are not the driver.",
        (
            "Discount and refund rates are measured on the acquired cohort's "
            "90-day order window."
        ),
    ),
    known_non_drivers=(
        "A material Affiliate acquisition-volume change.",
        "A broad COGS shock independent of realized revenue.",
        "A missing-day, broken-key, or undocumented-null defect.",
    ),
    expected_data_quality_findings=_COMMON_DATA_QUALITY_FINDINGS,
    ground_truth=(
        GroundTruthMetric(
            id="affiliate-q2-discount-rate",
            description=(
                "Affiliate Q2 discount-to-gross-revenue rate difference versus "
                "Affiliate Q1."
            ),
            comparison="discount_rate_affiliate_q2_minus_q1_90_day",
            metric_key="discount_rate",
            dimensions={"channel": "Affiliate"},
            baseline_period="Q1 2025",
            comparison_period="Q2 2025",
            comparison_type=MetricComparisonType.ABSOLUTE_DIFFERENCE,
            value_unit="fraction",
            expected_relative_change=0.05,
            tolerance=0.015,
            definition_context=_cohort_context(
                numerator="discount",
                denominator="gross revenue",
                definition_ref="discount_to_gross_revenue_rate_90_day",
            ),
        ),
        GroundTruthMetric(
            id="affiliate-q2-refund-rate",
            description=(
                "Affiliate Q2 refund-to-gross-revenue rate difference versus "
                "Affiliate Q1."
            ),
            comparison="refund_rate_affiliate_q2_minus_q1_90_day",
            metric_key="refund_rate",
            dimensions={"channel": "Affiliate"},
            baseline_period="Q1 2025",
            comparison_period="Q2 2025",
            comparison_type=MetricComparisonType.ABSOLUTE_DIFFERENCE,
            value_unit="fraction",
            expected_relative_change=0.04,
            tolerance=0.015,
            definition_context=_cohort_context(
                numerator="refund",
                denominator="gross revenue",
                definition_ref="refund_to_gross_revenue_rate_90_day",
            ),
        ),
        GroundTruthMetric(
            id="affiliate-q2-acquired-customers",
            description="Affiliate Q2 acquired-customer volume versus Affiliate Q1.",
            comparison="acquired_affiliate_q2_vs_q1",
            metric_key="acquired_customers",
            dimensions={"channel": "Affiliate"},
            baseline_period="Q1 2025",
            comparison_period="Q2 2025",
            comparison_type=MetricComparisonType.RELATIVE_CHANGE,
            expected_relative_change=0.0,
            tolerance=0.08,
            definition_context=_cohort_context(
                numerator="acquired customers",
                denominator=None,
                definition_ref="acquired_customer_count",
            ),
        ),
    ),
)


BUSINESS_ROOT_CAUSE_SCENARIOS = (
    RETENTION_DETERIORATION_SCENARIO,
    COGS_MARGIN_DETERIORATION_SCENARIO,
    DISCOUNT_REFUND_DETERIORATION_SCENARIO,
)


__all__ = [
    "BUSINESS_ROOT_CAUSE_SCENARIOS",
    "COGS_MARGIN_DETERIORATION_SCENARIO",
    "DISCOUNT_REFUND_DETERIORATION_SCENARIO",
    "RETENTION_DETERIORATION_SCENARIO",
]
