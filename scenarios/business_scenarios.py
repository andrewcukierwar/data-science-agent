"""Deterministic business root-cause scenario generators and observers.

The transformations in this module operate only on the clean ecommerce
baseline.  Their injected conditions and measured ground truth stay in the
evaluator-only scenario definitions; generated business documentation contains
only neutral metric definitions.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from scenarios.definitions import (
    COGS_MARGIN_DETERIORATION_SCENARIO,
    DISCOUNT_REFUND_DETERIORATION_SCENARIO,
    RETENTION_DETERIORATION_SCENARIO,
)
from scenarios.definitions.models import ScenarioDefinition
from scenarios.generator import (
    SyntheticEcommerceConfig,
    SyntheticEcommerceDataset,
    SyntheticEcommerceGenerator,
)
from scenarios.injection import ScenarioRun
from schemas.metrics import MetricComparison

_NEUTRAL_BUSINESS_DEFINITIONS = """

## Cohort and revenue-realization definitions

- **Acquired-customer retention rate**: the number of customers acquired in a
  reporting period who place at least two orders within 90 days of acquisition,
  divided by the number of customers acquired in that period.
- **COGS-to-net-revenue ratio**: the sum of `cogs` divided by the sum of
  `net_revenue` for the same acquisition cohort and 90-day order window.
- **Discount rate**: the sum of order-level `discount` divided by the sum of
  `gross_revenue` for the same acquisition cohort and 90-day order window.
- **Refund rate**: the sum of order-level `refund` divided by the sum of
  `gross_revenue` for the same acquisition cohort and 90-day order window.
- **Net revenue realization**: `gross_revenue - discount - refund` for each
  order. Rates use gross revenue as their denominator unless stated otherwise.
""".strip()


class RetentionScenarioInjectionConfig(BaseModel):
    """Reproducible parameters for the retention scenario."""

    model_config = ConfigDict(extra="forbid")

    target_channel: str = Field(default="Email", min_length=1)
    target_quarter: int = Field(default=2, ge=1, le=4)
    retention_multiplier: float = Field(default=0.70, gt=0, le=1)


class CogsMarginScenarioInjectionConfig(BaseModel):
    """Reproducible parameters for the COGS/margin scenario."""

    model_config = ConfigDict(extra="forbid")

    target_channel: str = Field(default="Google", min_length=1)
    target_quarter: int = Field(default=2, ge=1, le=4)
    margin_delta: float = Field(default=0.12, gt=0, lt=0.50)


class DiscountRefundScenarioInjectionConfig(BaseModel):
    """Reproducible parameters for the discount/refund scenario."""

    model_config = ConfigDict(extra="forbid")

    target_channel: str = Field(default="Affiliate", min_length=1)
    target_quarter: int = Field(default=2, ge=1, le=4)
    discount_rate_delta: float = Field(default=0.05, gt=0, lt=0.50)
    refund_rate_delta: float = Field(default=0.04, gt=0, lt=0.50)

    @model_validator(mode="after")
    def combined_rate_is_valid(self) -> DiscountRefundScenarioInjectionConfig:
        if self.discount_rate_delta + self.refund_rate_delta >= 0.80:
            raise ValueError("discount and refund deltas leave insufficient revenue")
        return self


def _append_neutral_definitions(dataset: SyntheticEcommerceDataset) -> str:
    return (
        dataset.business_definitions.rstrip()
        + "\n"
        + _NEUTRAL_BUSINESS_DEFINITIONS
        + "\n"
    )


def _quarter_mask(values: pd.Series, quarter: int) -> pd.Series:
    return pd.to_datetime(values).dt.quarter.eq(quarter)


def _cohort_customer_ids(
    customers: pd.DataFrame,
    *,
    channel: str,
    quarter: int,
) -> set[str]:
    mask = _quarter_mask(customers["acquisition_date"], quarter)
    return set(
        customers.loc[
            mask & customers["acquisition_channel"].eq(channel), "customer_id"
        ].astype(str)
    )


def _cohort_orders(
    dataset: SyntheticEcommerceDataset,
    *,
    channel: str,
    quarter: int,
    window_days: int = 90,
) -> pd.DataFrame:
    """Return order rows for a channel's acquisition cohort and window."""

    customers = dataset.customers
    ids = _cohort_customer_ids(customers, channel=channel, quarter=quarter)
    acquisition_dates = customers.set_index("customer_id")["acquisition_date"]
    order_acquisition_dates = dataset.orders["customer_id"].map(acquisition_dates)
    ages = (
        pd.to_datetime(dataset.orders["order_date"])
        - pd.to_datetime(order_acquisition_dates)
    ).dt.days
    mask = dataset.orders["customer_id"].isin(ids) & ages.between(0, window_days)
    return dataset.orders.loc[mask].copy()


def _cohort_retention_rate(
    dataset: SyntheticEcommerceDataset,
    *,
    channel: str,
    quarter: int,
) -> float:
    ids = _cohort_customer_ids(
        dataset.customers,
        channel=channel,
        quarter=quarter,
    )
    if not ids:
        raise ValueError(f"no customers for {channel} Q{quarter}")
    orders = _cohort_orders(dataset, channel=channel, quarter=quarter)
    counts = orders.groupby("customer_id", sort=True).size()
    return float(counts.reindex(sorted(ids), fill_value=0).gt(1).sum() / len(ids))


def _cohort_ratio(
    dataset: SyntheticEcommerceDataset,
    *,
    channel: str,
    quarter: int,
    numerator: str,
    denominator: str,
) -> float:
    orders = _cohort_orders(dataset, channel=channel, quarter=quarter)
    denominator_total = float(orders[denominator].sum())
    if denominator_total <= 0:
        raise ValueError(
            f"{channel} Q{quarter} has no positive {denominator} in the 90-day window"
        )
    return float(orders[numerator].sum()) / denominator_total


def _acquired_customers(
    dataset: SyntheticEcommerceDataset, channel: str, quarter: int
) -> int:
    return len(
        _cohort_customer_ids(
            dataset.customers,
            channel=channel,
            quarter=quarter,
        )
    )


def _marketing_spend(
    dataset: SyntheticEcommerceDataset, channel: str, quarter: int
) -> float:
    mask = _quarter_mask(dataset.marketing_spend["date"], quarter)
    return float(
        dataset.marketing_spend.loc[
            mask & dataset.marketing_spend["channel"].eq(channel), "spend"
        ].sum()
    )


def _relative_change(current: float, baseline: float) -> float:
    if baseline <= 0:
        raise ValueError("baseline metric must be positive for a relative change")
    return current / baseline - 1.0


def _comparison_set(
    definition: ScenarioDefinition,
    values: Mapping[str, float],
) -> tuple[MetricComparison, ...]:
    return tuple(
        MetricComparison(
            metric_key=metric.metric_key,
            dimensions=metric.dimensions,
            baseline_period=metric.baseline_period,
            comparison_period=metric.comparison_period,
            comparison_type=metric.comparison_type,
            value=values[metric.id],
            unit=metric.value_unit,
            evidence_refs=[f"generated-ground-truth:{metric.id}"],
            definition_context=metric.definition_context,
        )
        for metric in definition.ground_truth
    )


def _validate_target_channel(
    dataset: SyntheticEcommerceDataset,
    channel: str,
    quarter: int,
) -> None:
    if channel not in set(dataset.customers["acquisition_channel"]):
        raise ValueError(f"baseline does not contain acquisition channel {channel!r}")
    for table, column in (
        (dataset.sessions, "channel"),
        (dataset.marketing_spend, "channel"),
    ):
        if channel not in set(table[column]):
            raise ValueError(
                f"baseline does not contain {channel!r} in all channel tables"
            )
    if (
        _acquired_customers(dataset, channel, 1) == 0
        or _acquired_customers(dataset, channel, quarter) == 0
    ):
        raise ValueError(f"baseline must contain {channel} Q1 and Q{quarter} cohorts")


def _copy_dataset(
    baseline: SyntheticEcommerceDataset,
    *,
    orders: pd.DataFrame | None = None,
    business_definitions: str | None = None,
) -> SyntheticEcommerceDataset:
    return SyntheticEcommerceDataset(
        customers=baseline.customers.copy(deep=True),
        orders=(orders if orders is not None else baseline.orders.copy(deep=True)),
        sessions=baseline.sessions.copy(deep=True),
        marketing_spend=baseline.marketing_spend.copy(deep=True),
        business_definitions=business_definitions
        or _append_neutral_definitions(baseline),
    )


def inject_retention_deterioration(
    baseline: SyntheticEcommerceDataset,
    config: RetentionScenarioInjectionConfig | None = None,
) -> SyntheticEcommerceDataset:
    """Remove deterministic repeat orders from one acquisition cohort."""

    config = config or RetentionScenarioInjectionConfig()
    _validate_target_channel(baseline, config.target_channel, config.target_quarter)
    q1_rate = _cohort_retention_rate(baseline, channel=config.target_channel, quarter=1)
    q2_ids = _cohort_customer_ids(
        baseline.customers,
        channel=config.target_channel,
        quarter=config.target_quarter,
    )
    q2_orders = _cohort_orders(
        baseline,
        channel=config.target_channel,
        quarter=config.target_quarter,
    )
    counts = q2_orders.groupby("customer_id", sort=True).size()
    current_retained = set(counts[counts.gt(1)].index.astype(str))
    target_retained_count = round(len(q2_ids) * q1_rate * config.retention_multiplier)
    if target_retained_count >= len(current_retained):
        raise ValueError(
            "baseline Q2 cohort cannot support the requested retention deterioration"
        )
    remove_ids = sorted(current_retained)[target_retained_count:]
    customers = baseline.customers.set_index("customer_id")
    acquisition_dates = customers["acquisition_date"]
    order_ages = (
        pd.to_datetime(baseline.orders["order_date"])
        - pd.to_datetime(baseline.orders["customer_id"].map(acquisition_dates))
    ).dt.days
    candidate_mask = baseline.orders["customer_id"].isin(
        remove_ids
    ) & order_ages.between(0, 90)
    candidate = baseline.orders.loc[candidate_mask].copy()
    keep_indices = set(
        candidate.sort_values(["customer_id", "order_date", "order_id"])
        .groupby("customer_id", sort=True)
        .head(1)
        .index
    )
    remove_indices = candidate.index.difference(list(keep_indices))
    orders = baseline.orders.drop(index=remove_indices).reset_index(drop=True)
    return _copy_dataset(baseline, orders=orders)


def inject_cogs_margin_deterioration(
    baseline: SyntheticEcommerceDataset,
    config: CogsMarginScenarioInjectionConfig | None = None,
) -> SyntheticEcommerceDataset:
    """Raise COGS for one cohort while preserving order demand and revenue."""

    config = config or CogsMarginScenarioInjectionConfig()
    _validate_target_channel(baseline, config.target_channel, config.target_quarter)
    q1_ratio = _cohort_ratio(
        baseline,
        channel=config.target_channel,
        quarter=1,
        numerator="cogs",
        denominator="net_revenue",
    )
    target_ratio = q1_ratio + config.margin_delta
    if target_ratio >= 0.85:
        raise ValueError("requested COGS ratio would violate the net-revenue invariant")
    orders = baseline.orders.copy(deep=True)
    target_orders = _cohort_orders(
        baseline,
        channel=config.target_channel,
        quarter=config.target_quarter,
    )
    new_cogs = np.round(target_orders["net_revenue"] * target_ratio, 2)
    new_cogs = np.minimum(new_cogs, np.round(target_orders["net_revenue"] * 0.85, 2))
    orders.loc[target_orders.index, "cogs"] = new_cogs.to_numpy()
    return _copy_dataset(baseline, orders=orders)


def inject_discount_refund_deterioration(
    baseline: SyntheticEcommerceDataset,
    config: DiscountRefundScenarioInjectionConfig | None = None,
) -> SyntheticEcommerceDataset:
    """Raise discount and refund realization rates for one cohort."""

    config = config or DiscountRefundScenarioInjectionConfig()
    _validate_target_channel(baseline, config.target_channel, config.target_quarter)
    q1_discount_rate = _cohort_ratio(
        baseline,
        channel=config.target_channel,
        quarter=1,
        numerator="discount",
        denominator="gross_revenue",
    )
    q1_refund_rate = _cohort_ratio(
        baseline,
        channel=config.target_channel,
        quarter=1,
        numerator="refund",
        denominator="gross_revenue",
    )
    target_orders = _cohort_orders(
        baseline,
        channel=config.target_channel,
        quarter=config.target_quarter,
    )
    target_discount_rate = q1_discount_rate + config.discount_rate_delta
    target_refund_rate = q1_refund_rate + config.refund_rate_delta
    orders = baseline.orders.copy(deep=True)
    gross = target_orders["gross_revenue"]
    discount = np.minimum(
        np.round(gross * target_discount_rate, 2), np.round(gross * 0.80, 2)
    )
    refund = np.minimum(
        np.round(gross * target_refund_rate, 2),
        np.round(gross - discount, 2),
    )
    net_revenue = np.round(gross - discount - refund, 2)
    orders.loc[target_orders.index, "discount"] = discount.to_numpy()
    orders.loc[target_orders.index, "refund"] = refund.to_numpy()
    orders.loc[target_orders.index, "net_revenue"] = net_revenue.to_numpy()
    return _copy_dataset(baseline, orders=orders)


def generate_retention_deterioration_scenario(
    dataset_config: SyntheticEcommerceConfig | None = None,
    injection_config: RetentionScenarioInjectionConfig | None = None,
) -> ScenarioRun:
    dataset_config = dataset_config or SyntheticEcommerceConfig()
    injection_config = injection_config or RetentionScenarioInjectionConfig()
    baseline = SyntheticEcommerceGenerator(dataset_config).generate()
    return ScenarioRun(
        dataset=inject_retention_deterioration(baseline, injection_config),
        definition=RETENTION_DETERIORATION_SCENARIO,
        injection_config=injection_config,
    )


def generate_cogs_margin_deterioration_scenario(
    dataset_config: SyntheticEcommerceConfig | None = None,
    injection_config: CogsMarginScenarioInjectionConfig | None = None,
) -> ScenarioRun:
    dataset_config = dataset_config or SyntheticEcommerceConfig()
    injection_config = injection_config or CogsMarginScenarioInjectionConfig()
    baseline = SyntheticEcommerceGenerator(dataset_config).generate()
    return ScenarioRun(
        dataset=inject_cogs_margin_deterioration(baseline, injection_config),
        definition=COGS_MARGIN_DETERIORATION_SCENARIO,
        injection_config=injection_config,
    )


def generate_discount_refund_deterioration_scenario(
    dataset_config: SyntheticEcommerceConfig | None = None,
    injection_config: DiscountRefundScenarioInjectionConfig | None = None,
) -> ScenarioRun:
    dataset_config = dataset_config or SyntheticEcommerceConfig()
    injection_config = injection_config or DiscountRefundScenarioInjectionConfig()
    baseline = SyntheticEcommerceGenerator(dataset_config).generate()
    return ScenarioRun(
        dataset=inject_discount_refund_deterioration(baseline, injection_config),
        definition=DISCOUNT_REFUND_DETERIORATION_SCENARIO,
        injection_config=injection_config,
    )


def observe_retention_ground_truth(
    dataset: SyntheticEcommerceDataset,
) -> tuple[MetricComparison, ...]:
    channel = "Email"
    q1_rate = _cohort_retention_rate(dataset, channel=channel, quarter=1)
    q2_rate = _cohort_retention_rate(dataset, channel=channel, quarter=2)
    q1_customers = _acquired_customers(dataset, channel, 1)
    q2_customers = _acquired_customers(dataset, channel, 2)
    q1_cac = _marketing_spend(dataset, channel, 1) / q1_customers
    q2_cac = _marketing_spend(dataset, channel, 2) / q2_customers
    return _comparison_set(
        RETENTION_DETERIORATION_SCENARIO,
        {
            "email-q2-retention-rate": _relative_change(q2_rate, q1_rate),
            "email-q2-acquired-customers": _relative_change(q2_customers, q1_customers),
            "email-q2-cac": _relative_change(q2_cac, q1_cac),
        },
    )


def observe_cogs_margin_ground_truth(
    dataset: SyntheticEcommerceDataset,
) -> tuple[MetricComparison, ...]:
    channel = "Google"
    return _comparison_set(
        COGS_MARGIN_DETERIORATION_SCENARIO,
        {
            "google-q2-cogs-ratio": _cohort_ratio(
                dataset,
                channel=channel,
                quarter=2,
                numerator="cogs",
                denominator="net_revenue",
            )
            - _cohort_ratio(
                dataset,
                channel=channel,
                quarter=1,
                numerator="cogs",
                denominator="net_revenue",
            ),
            "google-q2-acquired-customers": _relative_change(
                _acquired_customers(dataset, channel, 2),
                _acquired_customers(dataset, channel, 1),
            ),
        },
    )


def observe_discount_refund_ground_truth(
    dataset: SyntheticEcommerceDataset,
) -> tuple[MetricComparison, ...]:
    channel = "Affiliate"
    return _comparison_set(
        DISCOUNT_REFUND_DETERIORATION_SCENARIO,
        {
            "affiliate-q2-discount-rate": _cohort_ratio(
                dataset,
                channel=channel,
                quarter=2,
                numerator="discount",
                denominator="gross_revenue",
            )
            - _cohort_ratio(
                dataset,
                channel=channel,
                quarter=1,
                numerator="discount",
                denominator="gross_revenue",
            ),
            "affiliate-q2-refund-rate": _cohort_ratio(
                dataset,
                channel=channel,
                quarter=2,
                numerator="refund",
                denominator="gross_revenue",
            )
            - _cohort_ratio(
                dataset,
                channel=channel,
                quarter=1,
                numerator="refund",
                denominator="gross_revenue",
            ),
            "affiliate-q2-acquired-customers": _relative_change(
                _acquired_customers(dataset, channel, 2),
                _acquired_customers(dataset, channel, 1),
            ),
        },
    )


__all__ = [
    "CogsMarginScenarioInjectionConfig",
    "DiscountRefundScenarioInjectionConfig",
    "RetentionScenarioInjectionConfig",
    "generate_cogs_margin_deterioration_scenario",
    "generate_discount_refund_deterioration_scenario",
    "generate_retention_deterioration_scenario",
    "inject_cogs_margin_deterioration",
    "inject_discount_refund_deterioration",
    "inject_retention_deterioration",
    "observe_cogs_margin_ground_truth",
    "observe_discount_refund_ground_truth",
    "observe_retention_ground_truth",
]
