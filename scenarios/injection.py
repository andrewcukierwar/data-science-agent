"""Deterministic transformations for the canonical business scenario."""

from dataclasses import dataclass
from datetime import date
from typing import ClassVar

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from scenarios.definitions import CANONICAL_PROFITABILITY_SCENARIO
from scenarios.definitions.models import ScenarioDefinition
from scenarios.generator import (
    SyntheticEcommerceConfig,
    SyntheticEcommerceDataset,
    SyntheticEcommerceGenerator,
)

_SCENARIO_DEFINITIONS = """

## Canonical Q2 scenario reporting definitions

- **Reporting contribution profit**: for a reporting period and acquisition
  channel, sum `net_revenue` for orders belonging to customers acquired in that
  period and channel, subtract their `cogs`, and subtract `marketing_spend`
  recorded for that channel during the same period. This is the scenario's
  reporting-level profitability metric:
  `SUM(net_revenue) - SUM(cogs) - SUM(marketing_spend)`.
- **Acquired-customer 90-day LTV**: for a customer cohort, average each
  customer's cumulative `net_revenue` from acquisition through 90 days after
  acquisition. LTV does not include marketing spend.
- **CAC**: channel marketing spend divided by the number of customers acquired
  through that channel in the same reporting period.
""".strip()


class CanonicalScenarioInjectionConfig(BaseModel):
    """Reproducible parameters for the canonical Q2 transformation."""

    model_config = ConfigDict(extra="forbid")

    meta_channel: str = Field(default="Meta", min_length=1)
    fallback_channel: str = Field(default="Organic", min_length=1)
    q2_start: date = date(2025, 4, 1)
    q2_end: date = date(2025, 6, 30)
    meta_q2_conversion_multiplier: float = Field(default=0.82, gt=0, le=1)
    meta_q2_customer_multiplier: float = Field(default=0.82, gt=0, le=1)
    meta_q2_spend_multiplier: float = Field(default=1.07, gt=0)

    @model_validator(mode="after")
    def q2_window_is_valid(self) -> "CanonicalScenarioInjectionConfig":
        if self.q2_end < self.q2_start:
            raise ValueError("q2_end must be on or after q2_start")
        return self

    @classmethod
    def for_dataset_config(
        cls,
        dataset_config: SyntheticEcommerceConfig,
    ) -> "CanonicalScenarioInjectionConfig":
        """Create matching Q2 dates for a baseline's calendar year."""

        year = dataset_config.start_date.year
        return cls(
            q2_start=date(year, 4, 1),
            q2_end=date(year, 6, 30),
        )


@dataclass(frozen=True, slots=True)
class ScenarioRun:
    """A transformed dataset paired with evaluator-only scenario metadata."""

    dataset: SyntheticEcommerceDataset
    definition: ScenarioDefinition
    injection_config: CanonicalScenarioInjectionConfig


class CanonicalProfitabilityScenarioInjector:
    """Apply the canonical Meta Q2 deterioration to a clean baseline."""

    definition: ClassVar[ScenarioDefinition] = CANONICAL_PROFITABILITY_SCENARIO

    def __init__(
        self,
        config: CanonicalScenarioInjectionConfig | None = None,
    ) -> None:
        self.config = config or CanonicalScenarioInjectionConfig()

    def inject(self, baseline: SyntheticEcommerceDataset) -> SyntheticEcommerceDataset:
        """Return a transformed copy without mutating the clean baseline."""

        customers = baseline.customers.copy(deep=True)
        orders = baseline.orders.copy(deep=True)
        sessions = baseline.sessions.copy(deep=True)
        marketing_spend = baseline.marketing_spend.copy(deep=True)
        self._validate_channels(customers, sessions, marketing_spend)

        q2_customer_mask = self._date_mask(
            customers["acquisition_date"],
            self.config.q2_start,
            self.config.q2_end,
        )
        q2_meta_customers = customers.loc[
            q2_customer_mask
            & customers["acquisition_channel"].eq(self.config.meta_channel)
        ].sort_values("customer_id")
        removed_customer_ids = self._select_removed_ids(
            q2_meta_customers["customer_id"],
            self.config.meta_q2_customer_multiplier,
        )
        customers.loc[
            customers["customer_id"].isin(removed_customer_ids),
            "acquisition_channel",
        ] = self.config.fallback_channel

        q2_session_mask = self._date_mask(
            sessions["session_date"],
            self.config.q2_start,
            self.config.q2_end,
        ) & sessions["channel"].eq(self.config.meta_channel)
        self._reduce_conversions(sessions, q2_session_mask)

        q2_spend_mask = self._date_mask(
            marketing_spend["date"],
            self.config.q2_start,
            self.config.q2_end,
        ) & marketing_spend["channel"].eq(self.config.meta_channel)
        self._validate_scenario_window(
            q2_meta_customers,
            q2_session_mask,
            sessions,
            q2_spend_mask,
        )
        self._increase_spend(marketing_spend, q2_spend_mask)

        return SyntheticEcommerceDataset(
            customers=customers,
            orders=orders,
            sessions=sessions,
            marketing_spend=marketing_spend,
            business_definitions=(
                baseline.business_definitions.rstrip()
                + "\n"
                + _SCENARIO_DEFINITIONS
                + "\n"
            ),
        )

    def _validate_channels(
        self,
        customers: pd.DataFrame,
        sessions: pd.DataFrame,
        marketing_spend: pd.DataFrame,
    ) -> None:
        customer_channels = set(customers["acquisition_channel"])
        session_channels = set(sessions["channel"])
        spend_channels = set(marketing_spend["channel"])
        if self.config.meta_channel not in (
            customer_channels & session_channels & spend_channels
        ):
            raise ValueError("baseline must contain Meta in all channel tables")
        if self.config.fallback_channel not in customer_channels:
            raise ValueError("baseline must contain the configured fallback channel")

    @staticmethod
    def _validate_scenario_window(
        q2_meta_customers: pd.DataFrame,
        q2_meta_session_mask: pd.Series,
        sessions: pd.DataFrame,
        q2_meta_spend_mask: pd.Series,
    ) -> None:
        if q2_meta_customers.empty:
            raise ValueError("baseline must contain Meta customers in the Q2 window")
        if (
            not q2_meta_session_mask.any()
            or not (q2_meta_session_mask & sessions["converted"].astype(bool)).any()
        ):
            raise ValueError(
                "baseline must contain converted Meta sessions in the Q2 window"
            )
        if not q2_meta_spend_mask.any():
            raise ValueError("baseline must contain Meta spend in the Q2 window")

    @staticmethod
    def _date_mask(
        values: pd.Series,
        start: date,
        end: date,
    ) -> pd.Series:
        if pd.api.types.is_datetime64_any_dtype(values):
            normalized = values.dt.date
        else:
            normalized = values
        return normalized.ge(start) & normalized.le(end)

    @staticmethod
    def _select_removed_ids(
        customer_ids: pd.Series,
        retention_multiplier: float,
    ) -> set[str]:
        remove_count = len(customer_ids) - round(
            len(customer_ids) * retention_multiplier
        )
        if remove_count <= 0:
            return set()
        positions = np.floor(
            np.arange(remove_count) * len(customer_ids) / remove_count
        ).astype(int)
        return set(customer_ids.iloc[positions])

    def _reduce_conversions(
        self,
        sessions: pd.DataFrame,
        q2_meta_mask: pd.Series,
    ) -> None:
        converted_indices = sessions.index[
            q2_meta_mask & sessions["converted"].astype(bool)
        ]
        keep_count = round(
            len(converted_indices) * self.config.meta_q2_conversion_multiplier
        )
        drop_count = len(converted_indices) - keep_count
        if drop_count <= 0:
            return
        positions = np.floor(
            np.arange(drop_count) * len(converted_indices) / drop_count
        ).astype(int)
        sessions.loc[converted_indices[positions], "converted"] = False

    def _increase_spend(
        self,
        marketing_spend: pd.DataFrame,
        q2_meta_mask: pd.Series,
    ) -> None:
        multiplier = self.config.meta_q2_spend_multiplier
        marketing_spend.loc[q2_meta_mask, "spend"] = np.round(
            marketing_spend.loc[q2_meta_mask, "spend"] * multiplier,
            2,
        )
        for column in ("impressions", "clicks"):
            marketing_spend.loc[q2_meta_mask, column] = np.rint(
                marketing_spend.loc[q2_meta_mask, column] * multiplier
            ).astype(marketing_spend[column].dtype)


def inject_canonical_profitability_scenario(
    baseline: SyntheticEcommerceDataset,
    config: CanonicalScenarioInjectionConfig | None = None,
) -> SyntheticEcommerceDataset:
    """Transform a clean baseline into the canonical Q2 scenario."""

    return CanonicalProfitabilityScenarioInjector(config).inject(baseline)


def generate_canonical_profitability_scenario(
    dataset_config: SyntheticEcommerceConfig | None = None,
    injection_config: CanonicalScenarioInjectionConfig | None = None,
) -> ScenarioRun:
    """Generate a clean baseline and apply the canonical scenario."""

    dataset_config = dataset_config or SyntheticEcommerceConfig()
    injection_config = injection_config or (
        CanonicalScenarioInjectionConfig.for_dataset_config(dataset_config)
    )
    baseline = SyntheticEcommerceGenerator(dataset_config).generate()
    transformed = CanonicalProfitabilityScenarioInjector(injection_config).inject(
        baseline
    )
    return ScenarioRun(
        dataset=transformed,
        definition=CANONICAL_PROFITABILITY_SCENARIO,
        injection_config=injection_config,
    )
