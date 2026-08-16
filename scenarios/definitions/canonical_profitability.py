"""Ground truth metadata for the canonical Q2 profitability scenario."""

from scenarios.definitions.models import (
    GroundTruthMetric,
    InjectedCondition,
    ScenarioDefinition,
)

CANONICAL_PROFITABILITY_SCENARIO = ScenarioDefinition(
    scenario_id="canonical-q2-profitability",
    name="Q2 Meta acquisition efficiency deterioration",
    user_question=(
        "Why did profitability decline in Q2, and what should the company do about it?"
    ),
    injected_conditions=(
        InjectedCondition(
            id="meta-q2-conversion-decline",
            description=(
                "Meta Q2 conversion declines by approximately 18%; a deterministic "
                "subset of would-be Meta-acquired customers is absent, together "
                "with its associated orders and sessions."
            ),
            affected_tables=("customers", "sessions", "orders"),
            relative_change=-0.18,
        ),
        InjectedCondition(
            id="meta-q2-spend-increase",
            description="Meta marketing spend increases by approximately 7% in Q2.",
            affected_tables=("marketing_spend",),
            relative_change=0.07,
        ),
        InjectedCondition(
            id="meta-q2-ltv-stability",
            description=(
                "Orders for retained Meta customers are unchanged, preserving "
                "their approximately stable 90-day acquired-customer LTV."
            ),
            affected_tables=("customers", "orders"),
            relative_change=0.0,
        ),
    ),
    expected_primary_driver=(
        "Meta acquisition efficiency deteriorated primarily because Q2 conversion "
        "declined, causing higher Meta CAC."
    ),
    expected_secondary_findings=(
        (
            "Meta Q2 CAC increases by approximately 30% because spend rises while "
            "acquired customers fall."
        ),
        "Meta Q2 reporting contribution profit declines for the acquired cohort.",
        "Meta Q2 acquired-customer 90-day LTV remains approximately stable.",
    ),
    known_non_drivers=(
        "A material decline in Meta acquired-customer 90-day LTV.",
        "A broad COGS or order-margin shock.",
        "A missing-day or broken-key data-quality defect.",
    ),
    expected_data_quality_findings=(
        "Customer IDs referenced by orders and sessions remain valid.",
        (
            "The scenario does not inject missing values, duplicate keys, or "
            "missing reporting days."
        ),
        "Business definitions explicitly define reporting contribution profit and LTV.",
    ),
    ground_truth=(
        GroundTruthMetric(
            id="meta-q2-conversion-rate",
            description="Meta Q2 converted-session rate versus Meta Q1.",
            comparison="scenario_meta_q2_conversion_vs_q1",
            metric_key="conversion_rate",
            dimensions={"channel": "Meta"},
            baseline_period="Q1 2025",
            comparison_period="Q2 2025",
            comparison_type="relative_change",
            value_unit="relative_change_fraction",
            expected_relative_change=-0.18,
            tolerance=0.03,
        ),
        GroundTruthMetric(
            id="meta-q2-acquired-customers",
            description="Meta Q2 acquired-customer count versus Meta Q1.",
            comparison="scenario_meta_q2_customer_count_vs_q1",
            metric_key="acquired_customers",
            dimensions={"channel": "Meta"},
            baseline_period="Q1 2025",
            comparison_period="Q2 2025",
            comparison_type="relative_change",
            value_unit="relative_change_fraction",
            expected_relative_change=-0.18,
            tolerance=0.01,
        ),
        GroundTruthMetric(
            id="meta-q2-spend",
            description="Meta Q2 marketing spend versus Meta Q1.",
            comparison="scenario_meta_q2_spend_vs_q1",
            metric_key="marketing_spend",
            dimensions={"channel": "Meta"},
            baseline_period="Q1 2025",
            comparison_period="Q2 2025",
            comparison_type="relative_change",
            value_unit="relative_change_fraction",
            expected_relative_change=0.07,
            tolerance=0.002,
        ),
        GroundTruthMetric(
            id="meta-q2-cac",
            description="Meta Q2 CAC versus Meta Q1.",
            comparison="scenario_meta_q2_cac_vs_q1",
            metric_key="cac",
            dimensions={"channel": "Meta"},
            baseline_period="Q1 2025",
            comparison_period="Q2 2025",
            comparison_type="relative_change",
            value_unit="relative_change_fraction",
            expected_relative_change=0.3078,
            tolerance=0.03,
        ),
        GroundTruthMetric(
            id="meta-q2-90-day-ltv",
            description=("Meta Q2 acquired-customer 90-day LTV versus Meta Q1."),
            comparison="scenario_meta_q2_ltv_vs_q1",
            metric_key="ltv",
            dimensions={"channel": "Meta"},
            baseline_period="Q1 2025",
            comparison_period="Q2 2025",
            comparison_type="relative_change",
            value_unit="relative_change_fraction",
            expected_relative_change=0.0,
            tolerance=0.05,
        ),
    ),
)
