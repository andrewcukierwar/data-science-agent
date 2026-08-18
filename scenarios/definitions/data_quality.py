"""Evaluator-only definitions for deterministic data-quality trap scenarios."""

from scenarios.definitions.models import (
    GroundTruthMetric,
    InjectedCondition,
    ScenarioDefinition,
)
from schemas.metrics import MetricComparisonType, MetricDefinitionContext

_COVERAGE_CONTEXT = MetricDefinitionContext(
    population="marketing spend calendar coverage",
    date_basis="marketing spend date",
    observation_window="calendar period",
    numerator="reporting days or channels present",
    denominator="expected calendar grid",
    definition_ref="daily_marketing_spend_coverage",
)


MISSING_REPORTING_DAY_SCENARIO = ScenarioDefinition(
    scenario_id="missing-reporting-day",
    name="Reporting coverage review",
    user_question=(
        "Assess whether the available reporting supports a reliable Q2 business "
        "comparison and describe any limitations."
    ),
    generation_config={
        "baseline": {"seed": 42, "start_date": "2025-01-01", "period_days": 365},
        "injection": {"date_offset": 150},
    },
    injected_conditions=(
        InjectedCondition(
            id="marketing-spend-calendar-gap",
            description=(
                "One interior calendar date is absent from the daily marketing "
                "spend source for every channel."
            ),
            affected_tables=("marketing_spend",),
            relative_change=None,
        ),
    ),
    expected_primary_driver=(
        "The source has a missing calendar reporting day; apparent business "
        "movement across that interval must not be interpreted before repair."
    ),
    expected_secondary_findings=(
        "The defect is isolated to daily marketing-spend coverage.",
        "Customer, order, and session key relationships remain valid.",
        "The analysis should distinguish event sparsity from a missing daily grid row.",
    ),
    known_non_drivers=(
        "A broken customer or order foreign key.",
        "A duplicate primary key in the source tables.",
        (
            "A business conclusion about demand or profitability before coverage "
            "is repaired."
        ),
    ),
    expected_data_quality_findings=(
        "Exactly one interior calendar day is missing from marketing-spend coverage.",
        "The marketing-spend daily grain is one row per channel per calendar date.",
        "The remaining source relationships and economic identities reconcile.",
    ),
    ground_truth=(
        GroundTruthMetric(
            id="marketing-spend-missing-day-count",
            description="Number of missing calendar days in marketing-spend coverage.",
            comparison="marketing_spend_missing_calendar_days",
            metric_key="missing_reporting_days",
            dimensions={"table": "marketing_spend"},
            baseline_period="expected calendar grid",
            comparison_period="observed source calendar",
            comparison_type=MetricComparisonType.ABSOLUTE_DIFFERENCE,
            value_unit="days",
            expected_relative_change=1.0,
            tolerance=0.0,
            definition_context=_COVERAGE_CONTEXT,
        ),
    ),
)


PARTIAL_LATEST_DAY_SCENARIO = ScenarioDefinition(
    scenario_id="partial-latest-reporting-day",
    name="Latest reporting cutoff review",
    user_question=(
        "Assess whether the latest available reporting period is complete enough "
        "for a business decision and describe any limitations."
    ),
    generation_config={
        "baseline": {"seed": 42, "start_date": "2025-01-01", "period_days": 365},
        "injection": {"latest_day": "period_end", "partial_channel": "Affiliate"},
    },
    injected_conditions=(
        InjectedCondition(
            id="latest-day-channel-truncation",
            description=(
                "The latest reporting date is present but one channel's daily "
                "marketing-spend row has not arrived."
            ),
            affected_tables=("marketing_spend",),
            relative_change=None,
        ),
    ),
    expected_primary_driver=(
        "The latest reporting day is partial, so current-period comparisons must "
        "be cut off or completed before interpretation."
    ),
    expected_secondary_findings=(
        "The defect affects the latest daily channel grid rather than all history.",
        "The source's key, date, and economic identities otherwise reconcile.",
        "A reporting cutoff limitation is distinct from a demand or margin conclusion.",
    ),
    known_non_drivers=(
        "A historical missing calendar day.",
        "A broken customer or order foreign key.",
        "A causal business explanation based on the incomplete latest day.",
    ),
    expected_data_quality_findings=(
        (
            "The latest reporting date has fewer channel rows than the documented "
            "daily grid."
        ),
        "Historical marketing-spend dates remain complete.",
        "The remaining source relationships and economic identities reconcile.",
    ),
    ground_truth=(
        GroundTruthMetric(
            id="latest-day-channel-coverage",
            description="Share of expected channel rows present on the latest date.",
            comparison="latest_marketing_spend_channel_coverage",
            metric_key="latest_day_channel_coverage",
            dimensions={"table": "marketing_spend"},
            baseline_period="expected channel grid",
            comparison_period="latest reporting day",
            comparison_type=MetricComparisonType.LEVEL,
            value_unit="fraction",
            expected_relative_change=0.8,
            tolerance=0.0,
            definition_context=_COVERAGE_CONTEXT,
        ),
    ),
)


DATA_QUALITY_SCENARIOS = (
    MISSING_REPORTING_DAY_SCENARIO,
    PARTIAL_LATEST_DAY_SCENARIO,
)


__all__ = [
    "DATA_QUALITY_SCENARIOS",
    "MISSING_REPORTING_DAY_SCENARIO",
    "PARTIAL_LATEST_DAY_SCENARIO",
]
