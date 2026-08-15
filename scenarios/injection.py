"""Deterministic transformations for the canonical business scenario."""

from dataclasses import dataclass
from datetime import date, timedelta
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

        year_start = date(self.config.q2_start.year, 1, 1)
        q1_end = self.config.q2_start - timedelta(days=1)
        q1_customer_mask = self._date_mask(
            customers["acquisition_date"], year_start, q1_end
        )
        q2_customer_mask = self._date_mask(
            customers["acquisition_date"],
            self.config.q2_start,
            self.config.q2_end,
        )
        q1_meta_customers = customers.loc[
            q1_customer_mask
            & customers["acquisition_channel"].eq(self.config.meta_channel)
        ].sort_values("customer_id")
        q2_meta_customers = customers.loc[
            q2_customer_mask
            & customers["acquisition_channel"].eq(self.config.meta_channel)
        ].sort_values("customer_id")
        q1_session_mask = self._date_mask(
            sessions["session_date"], year_start, q1_end
        ) & sessions["channel"].eq(self.config.meta_channel)
        q2_session_mask = self._date_mask(
            sessions["session_date"],
            self.config.q2_start,
            self.config.q2_end,
        ) & sessions["channel"].eq(self.config.meta_channel)
        self._validate_scenario_window(
            q2_meta_customers,
            q2_session_mask,
            sessions,
            self._date_mask(
                marketing_spend["date"],
                self.config.q2_start,
                self.config.q2_end,
            )
            & marketing_spend["channel"].eq(self.config.meta_channel),
        )
        q1_conversion_rate = float(
            sessions.loc[q1_session_mask, "converted"].astype(bool).mean()
        )
        if q1_conversion_rate <= 0:
            raise ValueError("baseline must contain converted Meta sessions in Q1")
        converted_q2_customer_ids = set(
            sessions.loc[
                q2_session_mask & sessions["converted"].astype(bool), "customer_id"
            ]
        )
        q2_session_customer_ids = set(sessions.loc[q2_session_mask, "customer_id"])
        removal_candidates = pd.concat(
            [
                q2_meta_customers.loc[
                    q2_meta_customers["customer_id"].isin(converted_q2_customer_ids),
                    "customer_id",
                ],
                q2_meta_customers.loc[
                    q2_meta_customers["customer_id"].isin(q2_session_customer_ids)
                    & ~q2_meta_customers["customer_id"].isin(converted_q2_customer_ids),
                    "customer_id",
                ],
                q2_meta_customers.loc[
                    ~q2_meta_customers["customer_id"].isin(q2_session_customer_ids),
                    "customer_id",
                ],
            ],
            ignore_index=True,
        ).drop_duplicates()
        removed_customer_ids = self._select_removed_ids(
            q2_meta_customers["customer_id"],
            len(q1_meta_customers),
            removal_candidates,
            self.config.meta_q2_customer_multiplier,
        )
        removed_mask = customers["customer_id"].isin(removed_customer_ids)
        customers = customers.loc[~removed_mask].reset_index(drop=True)
        orders = orders.loc[
            ~orders["customer_id"].isin(removed_customer_ids)
        ].reset_index(drop=True)
        sessions = sessions.loc[
            ~sessions["customer_id"].isin(removed_customer_ids)
        ].reset_index(drop=True)

        q2_session_mask = self._date_mask(
            sessions["session_date"],
            self.config.q2_start,
            self.config.q2_end,
        ) & sessions["channel"].eq(self.config.meta_channel)
        self._set_conversion_rate(
            sessions,
            q2_session_mask,
            target_rate=q1_conversion_rate * self.config.meta_q2_conversion_multiplier,
        )

        q1_spend_mask = self._date_mask(
            marketing_spend["date"], year_start, q1_end
        ) & marketing_spend["channel"].eq(self.config.meta_channel)
        q2_spend_mask = self._date_mask(
            marketing_spend["date"],
            self.config.q2_start,
            self.config.q2_end,
        ) & marketing_spend["channel"].eq(self.config.meta_channel)
        self._set_spend_relative_to_q1(
            marketing_spend,
            q1_spend_mask,
            q2_spend_mask,
        )

        q1_meta_customer_ids = set(q1_meta_customers["customer_id"])
        q2_meta_customer_ids = set(
            customers.loc[
                self._date_mask(
                    customers["acquisition_date"],
                    self.config.q2_start,
                    self.config.q2_end,
                )
                & customers["acquisition_channel"].eq(self.config.meta_channel),
                "customer_id",
            ]
        )
        q1_ltv = self._cohort_90_day_ltv(orders, customers, q1_meta_customer_ids)
        q2_ltv = self._cohort_90_day_ltv(orders, customers, q2_meta_customer_ids)
        if q1_ltv > 0 and q2_ltv <= 0:
            raise ValueError("baseline must contain Q2 Meta 90-day cohort revenue")
        if q2_ltv > 0:
            self._scale_cohort_orders(
                orders,
                q2_meta_customer_ids,
                current_ltv=q2_ltv,
                target_ltv=q1_ltv,
            )
        self._validate_order_revenue_identity(orders)
        self._validate_referential_integrity(customers, orders, sessions)

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
        q1_customer_count: int,
        preferred_ids: pd.Series,
        retention_multiplier: float,
    ) -> set[str]:
        target_count = round(q1_customer_count * retention_multiplier)
        remove_count = len(customer_ids) - target_count
        if target_count > len(customer_ids):
            raise ValueError("baseline Q2 Meta cohort is smaller than the target")
        if remove_count <= 0:
            return set()
        if target_count < 1:
            raise ValueError("scenario target must retain at least one Meta customer")
        ordered_candidates = pd.concat(
            [preferred_ids, customer_ids[~customer_ids.isin(preferred_ids)]]
        ).drop_duplicates()
        return set(ordered_candidates.iloc[:remove_count])

    @staticmethod
    def _cohort_90_day_ltv(
        orders: pd.DataFrame,
        customers: pd.DataFrame,
        customer_ids: set[str],
    ) -> float:
        if not customer_ids:
            return 0.0
        acquisition_dates = pd.to_datetime(
            customers.set_index("customer_id")["acquisition_date"]
        )
        cohort_orders = orders[orders["customer_id"].isin(customer_ids)].copy()
        order_age = pd.to_datetime(cohort_orders["order_date"]) - cohort_orders[
            "customer_id"
        ].map(acquisition_dates)
        in_window = order_age.between(pd.Timedelta(0), pd.Timedelta(days=90))
        revenue = (
            cohort_orders.loc[in_window].groupby("customer_id")["net_revenue"].sum()
        )
        return float(revenue.reindex(sorted(customer_ids), fill_value=0.0).mean())

    @staticmethod
    def _scale_cohort_orders(
        orders: pd.DataFrame,
        customer_ids: set[str],
        *,
        current_ltv: float,
        target_ltv: float,
    ) -> None:
        multiplier = target_ltv / current_ltv
        cohort_mask = orders["customer_id"].isin(customer_ids)
        for column in ("gross_revenue", "discount", "refund"):
            orders.loc[cohort_mask, column] = np.round(
                orders.loc[cohort_mask, column] * multiplier,
                2,
            )
        orders.loc[cohort_mask, "discount"] = np.minimum(
            orders.loc[cohort_mask, "discount"],
            orders.loc[cohort_mask, "gross_revenue"],
        )
        discounted_revenue = (
            orders.loc[cohort_mask, "gross_revenue"]
            - orders.loc[cohort_mask, "discount"]
        )
        orders.loc[cohort_mask, "refund"] = np.minimum(
            orders.loc[cohort_mask, "refund"], discounted_revenue
        )
        orders.loc[cohort_mask, "net_revenue"] = np.round(
            orders.loc[cohort_mask, "gross_revenue"]
            - orders.loc[cohort_mask, "discount"]
            - orders.loc[cohort_mask, "refund"],
            2,
        )
        orders.loc[cohort_mask, "cogs"] = np.round(
            orders.loc[cohort_mask, "cogs"] * multiplier,
            2,
        )

    @staticmethod
    def _validate_order_revenue_identity(orders: pd.DataFrame) -> None:
        expected = np.round(
            orders["gross_revenue"] - orders["discount"] - orders["refund"],
            2,
        )
        if not np.allclose(expected, orders["net_revenue"], atol=0.001):
            raise ValueError("orders violate the documented net revenue identity")

    @staticmethod
    def _validate_referential_integrity(
        customers: pd.DataFrame,
        orders: pd.DataFrame,
        sessions: pd.DataFrame,
    ) -> None:
        customer_ids = set(customers["customer_id"])
        if not set(orders["customer_id"]).issubset(customer_ids):
            raise ValueError("scenario orders reference an absent customer")
        if not set(sessions["customer_id"]).issubset(customer_ids):
            raise ValueError("scenario sessions reference an absent customer")

    @staticmethod
    def _set_conversion_rate(
        sessions: pd.DataFrame,
        q2_meta_mask: pd.Series,
        *,
        target_rate: float,
    ) -> None:
        session_indices = sessions.index[q2_meta_mask]
        target_count = round(len(session_indices) * target_rate)
        converted_indices = sessions.index[
            q2_meta_mask & sessions["converted"].astype(bool)
        ]
        if target_count < len(converted_indices):
            drop_count = len(converted_indices) - target_count
            positions = np.floor(
                np.arange(drop_count) * len(converted_indices) / drop_count
            ).astype(int)
            sessions.loc[converted_indices[positions], "converted"] = False
        elif target_count > len(converted_indices):
            unconverted_indices = sessions.index[
                q2_meta_mask & ~sessions["converted"].astype(bool)
            ]
            add_count = min(
                target_count - len(converted_indices), len(unconverted_indices)
            )
            if add_count <= 0:
                return
            positions = np.floor(
                np.arange(add_count) * len(unconverted_indices) / add_count
            ).astype(int)
            sessions.loc[unconverted_indices[positions], "converted"] = True

    def _set_spend_relative_to_q1(
        self,
        marketing_spend: pd.DataFrame,
        q1_meta_mask: pd.Series,
        q2_meta_mask: pd.Series,
    ) -> None:
        q1_spend = float(marketing_spend.loc[q1_meta_mask, "spend"].sum())
        q2_spend = float(marketing_spend.loc[q2_meta_mask, "spend"].sum())
        if q1_spend <= 0 or q2_spend <= 0:
            raise ValueError("baseline must contain positive Meta spend in Q1 and Q2")
        target_q2_spend = q1_spend * self.config.meta_q2_spend_multiplier
        multiplier = target_q2_spend / q2_spend
        marketing_spend.loc[q2_meta_mask, "spend"] = np.round(
            marketing_spend.loc[q2_meta_mask, "spend"] * multiplier,
            2,
        )
        for column in ("impressions", "clicks"):
            marketing_spend.loc[q2_meta_mask, column] = np.rint(
                marketing_spend.loc[q2_meta_mask, column] * multiplier
            ).astype(marketing_spend[column].dtype)
        marketing_spend.loc[q2_meta_mask, "clicks"] = np.minimum(
            marketing_spend.loc[q2_meta_mask, "clicks"],
            marketing_spend.loc[q2_meta_mask, "impressions"],
        )


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
