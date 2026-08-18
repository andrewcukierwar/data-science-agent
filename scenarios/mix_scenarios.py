"""Deterministic acquisition-channel mix confounding transformation."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from scenarios.definitions import CHANNEL_MIX_CONFOUNDING_SCENARIO
from scenarios.definitions.models import ScenarioDefinition
from scenarios.generator import (
    SyntheticEcommerceConfig,
    SyntheticEcommerceDataset,
    SyntheticEcommerceGenerator,
)
from scenarios.injection import ScenarioRun
from schemas.metrics import MetricComparison

_NEUTRAL_MIX_DEFINITIONS = """

## Acquisition-channel mix definitions

- **Acquired-customer channel share**: acquired customers attributed to a
  channel divided by all acquired customers in the same reporting quarter.
- **Total acquired customers**: each customer is counted once using the
  customer's `acquisition_date`, independent of order count.
- Channel shares describe attribution composition and should be read with the
  reporting period and denominator stated above.
""".strip()


class ChannelMixScenarioInjectionConfig(BaseModel):
    """Reproducible attribution mix shift for one acquisition quarter."""

    model_config = ConfigDict(extra="forbid")

    source_channel: str = Field(default="Meta", min_length=1)
    destination_channel: str = Field(default="Organic", min_length=1)
    target_quarter: int = Field(default=2, ge=1, le=4)
    mix_fraction: float = Field(default=0.30, gt=0, lt=1)

    @model_validator(mode="after")
    def channels_are_distinct(self) -> ChannelMixScenarioInjectionConfig:
        if self.source_channel == self.destination_channel:
            raise ValueError("source and destination channels must differ")
        return self


def _append_mix_definitions(dataset: SyntheticEcommerceDataset) -> str:
    return (
        dataset.business_definitions.rstrip() + "\n" + _NEUTRAL_MIX_DEFINITIONS + "\n"
    )


def _quarter_mask(values: pd.Series, quarter: int) -> pd.Series:
    return pd.to_datetime(values).dt.quarter.eq(quarter)


def _copy_dataset(
    baseline: SyntheticEcommerceDataset,
    *,
    customers: pd.DataFrame,
    sessions: pd.DataFrame,
) -> SyntheticEcommerceDataset:
    return SyntheticEcommerceDataset(
        customers=customers,
        orders=baseline.orders.copy(deep=True),
        sessions=sessions,
        marketing_spend=baseline.marketing_spend.copy(deep=True),
        business_definitions=_append_mix_definitions(baseline),
    )


def inject_channel_mix_confounding(
    baseline: SyntheticEcommerceDataset,
    config: ChannelMixScenarioInjectionConfig | None = None,
) -> SyntheticEcommerceDataset:
    """Reattribute a deterministic Q2 customer subset without changing volume."""

    config = config or ChannelMixScenarioInjectionConfig()
    customers = baseline.customers.copy(deep=True)
    sessions = baseline.sessions.copy(deep=True)
    channels = set(customers["acquisition_channel"])
    if (
        config.source_channel not in channels
        or config.destination_channel not in channels
    ):
        raise ValueError("both mix channels must exist in the baseline")

    target = _quarter_mask(customers["acquisition_date"], config.target_quarter)
    source = target & customers["acquisition_channel"].eq(config.source_channel)
    source_ids = sorted(customers.loc[source, "customer_id"].astype(str))
    move_count = round(len(source_ids) * config.mix_fraction)
    if move_count < 1:
        raise ValueError("mix shift must move at least one acquired customer")
    moved_ids = set(source_ids[:move_count])
    moved = customers["customer_id"].astype(str).isin(moved_ids)
    customers.loc[moved, "acquisition_channel"] = config.destination_channel

    converted = sessions["converted"].astype(bool)
    session_customer_ids = sessions["customer_id"].astype("string")
    moved_sessions = converted & session_customer_ids.isin(moved_ids)
    sessions.loc[moved_sessions, "channel"] = config.destination_channel
    return _copy_dataset(baseline, customers=customers, sessions=sessions)


def _share(customers: pd.DataFrame, channel: str, quarter: int) -> float:
    period = customers.loc[_quarter_mask(customers["acquisition_date"], quarter)]
    if period.empty:
        raise ValueError(f"quarter {quarter} has no acquired customers")
    return float(period["acquisition_channel"].eq(channel).mean())


def _count(customers: pd.DataFrame, quarter: int) -> int:
    return int(_quarter_mask(customers["acquisition_date"], quarter).sum())


def _relative_change(current: float, baseline: float) -> float:
    if baseline <= 0:
        raise ValueError("baseline count must be positive")
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


def generate_channel_mix_confounding_scenario(
    dataset_config: SyntheticEcommerceConfig | None = None,
    injection_config: ChannelMixScenarioInjectionConfig | None = None,
) -> ScenarioRun:
    dataset_config = dataset_config or SyntheticEcommerceConfig()
    injection_config = injection_config or ChannelMixScenarioInjectionConfig()
    baseline = SyntheticEcommerceGenerator(dataset_config).generate()
    return ScenarioRun(
        dataset=inject_channel_mix_confounding(baseline, injection_config),
        definition=CHANNEL_MIX_CONFOUNDING_SCENARIO,
        injection_config=injection_config,
    )


def observe_channel_mix_ground_truth(
    dataset: SyntheticEcommerceDataset,
) -> tuple[MetricComparison, ...]:
    customers = dataset.customers
    q1_total = _count(customers, 1)
    q2_total = _count(customers, 2)
    return _comparison_set(
        CHANNEL_MIX_CONFOUNDING_SCENARIO,
        {
            "meta-q2-acquisition-share": _share(customers, "Meta", 2)
            - _share(customers, "Meta", 1),
            "organic-q2-acquisition-share": _share(customers, "Organic", 2)
            - _share(customers, "Organic", 1),
            "total-q2-acquired-customers": _relative_change(q2_total, q1_total),
        },
    )


__all__ = [
    "ChannelMixScenarioInjectionConfig",
    "generate_channel_mix_confounding_scenario",
    "inject_channel_mix_confounding",
    "observe_channel_mix_ground_truth",
]
