"""Deterministic missing-day and partial-latest-day source traps."""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from scenarios.definitions import (
    MISSING_REPORTING_DAY_SCENARIO,
    PARTIAL_LATEST_DAY_SCENARIO,
)
from scenarios.definitions.models import ScenarioDefinition
from scenarios.generator import (
    SyntheticEcommerceConfig,
    SyntheticEcommerceDataset,
    SyntheticEcommerceGenerator,
)
from scenarios.injection import ScenarioRun
from schemas.metrics import MetricComparison

_NEUTRAL_COVERAGE_DEFINITIONS = """

## Source coverage definitions

- **Daily marketing-spend grain**: the `marketing_spend` source has one row for
  each configured acquisition channel on each calendar date in the reporting
  period.
- **Calendar coverage**: expected dates are the inclusive calendar range from
  the earliest to latest reporting date. Channel coverage is evaluated within
  each date, not inferred from order-event sparsity.
- **Latest reporting date**: the maximum date present in the source. A latest
  date with incomplete channel coverage is not a complete reporting day.
""".strip()


class MissingReportingDayInjectionConfig(BaseModel):
    """Reproducible location of the interior calendar gap."""

    model_config = ConfigDict(extra="forbid")

    date_offset: int = Field(default=150, ge=1)


class PartialLatestDayInjectionConfig(BaseModel):
    """Reproducible channel omitted from the latest reporting date."""

    model_config = ConfigDict(extra="forbid")

    partial_channel: str = Field(default="Affiliate", min_length=1)


def _append_coverage_definitions(dataset: SyntheticEcommerceDataset) -> str:
    return (
        dataset.business_definitions.rstrip()
        + "\n"
        + _NEUTRAL_COVERAGE_DEFINITIONS
        + "\n"
    )


def _coverage_comparison(
    definition: ScenarioDefinition,
    value: float,
) -> tuple[MetricComparison, ...]:
    metric = definition.ground_truth[0]
    return (
        MetricComparison(
            metric_key=metric.metric_key,
            dimensions=metric.dimensions,
            baseline_period=metric.baseline_period,
            comparison_period=metric.comparison_period,
            comparison_type=metric.comparison_type,
            value=value,
            unit=metric.value_unit,
            evidence_refs=[f"generated-ground-truth:{metric.id}"],
            definition_context=metric.definition_context,
        ),
    )


def _date_series(frame: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(frame["date"], errors="raise").dt.date


def inject_missing_reporting_day(
    baseline: SyntheticEcommerceDataset,
    config: MissingReportingDayInjectionConfig | None = None,
) -> SyntheticEcommerceDataset:
    """Remove every channel row for one interior marketing date."""

    config = config or MissingReportingDayInjectionConfig()
    dates = sorted(_date_series(baseline.marketing_spend).unique())
    if config.date_offset >= len(dates) - 1:
        raise ValueError("date_offset must select an interior reporting date")
    target_date = dates[config.date_offset]
    spend = baseline.marketing_spend.copy(deep=True)
    spend = spend.loc[_date_series(spend) != target_date].reset_index(drop=True)
    return SyntheticEcommerceDataset(
        customers=baseline.customers.copy(deep=True),
        orders=baseline.orders.copy(deep=True),
        sessions=baseline.sessions.copy(deep=True),
        marketing_spend=spend,
        business_definitions=_append_coverage_definitions(baseline),
    )


def inject_partial_latest_reporting_day(
    baseline: SyntheticEcommerceDataset,
    config: PartialLatestDayInjectionConfig | None = None,
) -> SyntheticEcommerceDataset:
    """Remove one channel row from the maximum marketing-spend date."""

    config = config or PartialLatestDayInjectionConfig()
    spend = baseline.marketing_spend.copy(deep=True)
    if config.partial_channel not in set(spend["channel"]):
        raise ValueError(
            f"baseline does not contain channel {config.partial_channel!r}"
        )
    dates = _date_series(spend)
    latest = dates.max()
    remove = dates.eq(latest) & spend["channel"].eq(config.partial_channel)
    if not remove.any():
        raise ValueError("baseline has no target channel row on its latest date")
    spend = spend.loc[~remove].reset_index(drop=True)
    return SyntheticEcommerceDataset(
        customers=baseline.customers.copy(deep=True),
        orders=baseline.orders.copy(deep=True),
        sessions=baseline.sessions.copy(deep=True),
        marketing_spend=spend,
        business_definitions=_append_coverage_definitions(baseline),
    )


def _missing_day_count(dataset: SyntheticEcommerceDataset) -> int:
    dates = _date_series(dataset.marketing_spend)
    expected = (dates.max() - dates.min()).days + 1
    return int(expected - dates.nunique())


def _latest_channel_coverage(dataset: SyntheticEcommerceDataset) -> float:
    spend = dataset.marketing_spend
    dates = _date_series(spend)
    latest = dates.max()
    expected_channels = set(spend["channel"])
    present_channels = set(spend.loc[dates.eq(latest), "channel"])
    if not expected_channels:
        raise ValueError("marketing spend must contain at least one channel")
    return len(present_channels) / len(expected_channels)


def generate_missing_reporting_day_scenario(
    dataset_config: SyntheticEcommerceConfig | None = None,
    injection_config: MissingReportingDayInjectionConfig | None = None,
) -> ScenarioRun:
    dataset_config = dataset_config or SyntheticEcommerceConfig()
    injection_config = injection_config or MissingReportingDayInjectionConfig()
    baseline = SyntheticEcommerceGenerator(dataset_config).generate()
    return ScenarioRun(
        dataset=inject_missing_reporting_day(baseline, injection_config),
        definition=MISSING_REPORTING_DAY_SCENARIO,
        injection_config=injection_config,
    )


def generate_partial_latest_reporting_day_scenario(
    dataset_config: SyntheticEcommerceConfig | None = None,
    injection_config: PartialLatestDayInjectionConfig | None = None,
) -> ScenarioRun:
    dataset_config = dataset_config or SyntheticEcommerceConfig()
    injection_config = injection_config or PartialLatestDayInjectionConfig()
    baseline = SyntheticEcommerceGenerator(dataset_config).generate()
    return ScenarioRun(
        dataset=inject_partial_latest_reporting_day(baseline, injection_config),
        definition=PARTIAL_LATEST_DAY_SCENARIO,
        injection_config=injection_config,
    )


def observe_missing_reporting_day_ground_truth(
    dataset: SyntheticEcommerceDataset,
) -> tuple[MetricComparison, ...]:
    return _coverage_comparison(
        MISSING_REPORTING_DAY_SCENARIO,
        float(_missing_day_count(dataset)),
    )


def observe_partial_latest_reporting_day_ground_truth(
    dataset: SyntheticEcommerceDataset,
) -> tuple[MetricComparison, ...]:
    return _coverage_comparison(
        PARTIAL_LATEST_DAY_SCENARIO,
        _latest_channel_coverage(dataset),
    )


__all__ = [
    "MissingReportingDayInjectionConfig",
    "PartialLatestDayInjectionConfig",
    "generate_missing_reporting_day_scenario",
    "generate_partial_latest_reporting_day_scenario",
    "inject_missing_reporting_day",
    "inject_partial_latest_reporting_day",
    "observe_missing_reporting_day_ground_truth",
    "observe_partial_latest_reporting_day_ground_truth",
]
