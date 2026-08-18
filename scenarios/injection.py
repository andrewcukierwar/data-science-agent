"""Deterministic transformations for the canonical business scenario."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import ClassVar, Protocol

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
from schemas.metrics import MetricComparison

_SCENARIO_DEFINITIONS = """

## Canonical Q2 scenario reporting definitions

- **Reporting contribution profit**: for a reporting period and acquisition
  channel, sum `net_revenue - cogs` for orders from customers acquired in that
  period and channel when the order falls from `acquisition_date` through
  `acquisition_date + 90 days`, then subtract `marketing_spend` recorded for
  that channel during the acquisition period. The same 90-day observation
  window is used for Q1 and Q2:
  `SUM(net_revenue - cogs) - SUM(marketing_spend)`.
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


class ScenarioDataset(Protocol):
    """Common source-bundle surface shared by scenario families."""

    business_definitions: str

    def table_map(self) -> dict[str, pd.DataFrame]:
        """Return named, read-only source tables."""


@dataclass(frozen=True, slots=True)
class ScenarioRun:
    """A transformed dataset paired with evaluator-only scenario metadata."""

    dataset: ScenarioDataset
    definition: ScenarioDefinition
    injection_config: BaseModel


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
        q1_session_mask = self._date_mask(
            sessions["session_date"], year_start, q1_end
        ) & sessions["channel"].eq(self.config.meta_channel)
        q2_session_mask = self._date_mask(
            sessions["session_date"],
            self.config.q2_start,
            self.config.q2_end,
        ) & sessions["channel"].eq(self.config.meta_channel)
        self._validate_scenario_window(
            q2_session_mask,
            sessions,
            self._date_mask(
                marketing_spend["date"],
                self.config.q2_start,
                self.config.q2_end,
            )
            & marketing_spend["channel"].eq(self.config.meta_channel),
        )
        self._rebalance_meta_session_volume(
            sessions,
            q1_session_mask,
            q2_session_mask,
            q1_start=year_start,
            q1_end=q1_end,
            q2_start=self.config.q2_start,
            q2_end=self.config.q2_end,
        )
        q1_session_mask = self._date_mask(
            sessions["session_date"], year_start, q1_end
        ) & sessions["channel"].eq(self.config.meta_channel)
        q2_session_mask = self._date_mask(
            sessions["session_date"],
            self.config.q2_start,
            self.config.q2_end,
        ) & sessions["channel"].eq(self.config.meta_channel)

        q1_converted_count = int(
            sessions.loc[q1_session_mask, "converted"].astype(bool).sum()
        )
        q2_converted_indices = sessions.index[
            q2_session_mask & sessions["converted"].astype(bool)
        ]
        if q1_converted_count <= 0:
            raise ValueError("baseline must contain converted Meta sessions in Q1")
        target_q2_converted_count = round(
            q1_converted_count * self.config.meta_q2_conversion_multiplier
        )
        if target_q2_converted_count < 1:
            raise ValueError("scenario must retain at least one Q2 Meta acquisition")
        if target_q2_converted_count > len(q2_converted_indices):
            raise ValueError(
                "baseline Q2 Meta acquisition traffic cannot support the requested "
                "conversion target"
            )
        removed_session_indices = sorted(
            q2_converted_indices,
            key=lambda index: str(sessions.at[index, "customer_id"]),
        )[: len(q2_converted_indices) - target_q2_converted_count]
        removed_customer_ids = set(
            sessions.loc[removed_session_indices, "customer_id"].astype(str)
        )
        if removed_session_indices:
            sessions.loc[removed_session_indices, "converted"] = False
            sessions.loc[removed_session_indices, "customer_id"] = None
        removed_mask = customers["customer_id"].isin(removed_customer_ids)
        customers = customers.loc[~removed_mask].reset_index(drop=True)
        orders = orders.loc[
            ~orders["customer_id"].isin(removed_customer_ids)
        ].reset_index(drop=True)
        sessions = sessions.loc[
            ~sessions["customer_id"].isin(removed_customer_ids)
        ].reset_index(drop=True)

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

        q1_meta_customer_ids = set(
            customers.loc[
                self._date_mask(customers["acquisition_date"], year_start, q1_end)
                & customers["acquisition_channel"].eq(self.config.meta_channel),
                "customer_id",
            ]
        )
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
        self._validate_acquisition_funnel(customers, sessions)
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
        q2_meta_session_mask: pd.Series,
        sessions: pd.DataFrame,
        q2_meta_spend_mask: pd.Series,
    ) -> None:
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
    def _rebalance_meta_session_volume(
        sessions: pd.DataFrame,
        q1_mask: pd.Series,
        q2_mask: pd.Series,
        *,
        q1_start: date,
        q1_end: date,
        q2_start: date,
        q2_end: date,
    ) -> None:
        """Keep Q1 and Q2 Meta acquisition traffic comparable.

        Only anonymous non-converting sessions may be moved between periods.
        Converted acquisition sessions remain anchored to their customers'
        acquisition dates, preserving the funnel invariant.
        """

        q1_count = int(q1_mask.sum())
        q2_count = int(q2_mask.sum())
        difference = q2_count - q1_count
        if abs(difference) <= 1:
            return

        if difference > 0:
            source_mask = q2_mask & sessions["customer_id"].isna()
            target_start, target_end = q1_start, q1_end
        else:
            source_mask = q1_mask & sessions["customer_id"].isna()
            target_start, target_end = q2_start, q2_end

        move_count = abs(difference) // 2
        source_indices = sessions.index[source_mask].sort_values()[:move_count]
        if len(source_indices) < move_count:
            raise ValueError(
                "baseline does not contain enough anonymous Meta acquisition "
                "traffic to balance Q1 and Q2"
            )
        target_dates = pd.date_range(target_start, target_end, periods=move_count)
        sessions.loc[source_indices, "session_date"] = target_dates.date

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
        session_customer_ids = set(sessions["customer_id"].dropna())
        if not session_customer_ids.issubset(customer_ids):
            raise ValueError("scenario sessions reference an absent customer")

    @staticmethod
    def _validate_acquisition_funnel(
        customers: pd.DataFrame,
        sessions: pd.DataFrame,
    ) -> None:
        """Validate the observable acquisition-session/customer invariant."""

        customer_ids = set(customers["customer_id"])
        converted = sessions["converted"].astype(bool)
        converted_sessions = sessions.loc[converted]
        if converted_sessions["customer_id"].isna().any():
            raise ValueError("converted acquisition sessions require customer IDs")
        if set(converted_sessions["customer_id"]) != customer_ids:
            raise ValueError(
                "each customer must correspond to exactly one converted "
                "acquisition session"
            )
        if converted_sessions["customer_id"].duplicated().any():
            raise ValueError("customers must have exactly one converted session")
        if sessions.loc[~converted, "customer_id"].notna().any():
            raise ValueError(
                "non-converting acquisition sessions must not claim a customer"
            )

        customer_lookup = customers.set_index("customer_id")
        converted_with_customer = converted_sessions.set_index("customer_id")
        if (
            not pd.to_datetime(converted_with_customer["session_date"])
            .eq(
                pd.to_datetime(
                    customer_lookup.loc[
                        converted_with_customer.index, "acquisition_date"
                    ]
                )
            )
            .all()
        ):
            raise ValueError("converted sessions must occur on acquisition_date")
        if (
            not converted_with_customer["channel"]
            .eq(
                customer_lookup.loc[
                    converted_with_customer.index, "acquisition_channel"
                ]
            )
            .all()
        ):
            raise ValueError("converted session channels must match acquisition")
        if (
            not converted_with_customer["device"]
            .eq(customer_lookup.loc[converted_with_customer.index, "device"])
            .all()
        ):
            raise ValueError("converted session devices must match customer devices")

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


def observe_canonical_ground_truth(
    dataset: SyntheticEcommerceDataset,
) -> tuple[MetricComparison, ...]:
    """Measure canonical ground truth directly from generated source tables."""

    customers = dataset.customers
    sessions = dataset.sessions
    spend = dataset.marketing_spend
    channel = "Meta"

    def quarter_mask(values: pd.Series, quarter: int) -> pd.Series:
        return pd.to_datetime(values).dt.quarter.eq(quarter)

    def customer_ids(quarter: int) -> set[str]:
        mask = quarter_mask(customers["acquisition_date"], quarter)
        return set(
            customers.loc[
                mask & customers["acquisition_channel"].eq(channel), "customer_id"
            ]
        )

    q1_customers = customer_ids(1)
    q2_customers = customer_ids(2)
    q1_sessions = quarter_mask(sessions["session_date"], 1) & sessions["channel"].eq(
        channel
    )
    q2_sessions = quarter_mask(sessions["session_date"], 2) & sessions["channel"].eq(
        channel
    )
    q1_spend = quarter_mask(spend["date"], 1) & spend["channel"].eq(channel)
    q2_spend = quarter_mask(spend["date"], 2) & spend["channel"].eq(channel)
    conversion_q1 = float(sessions.loc[q1_sessions, "converted"].mean())
    conversion_q2 = float(sessions.loc[q2_sessions, "converted"].mean())
    spend_q1 = float(spend.loc[q1_spend, "spend"].sum())
    spend_q2 = float(spend.loc[q2_spend, "spend"].sum())
    cac_q1 = spend_q1 / len(q1_customers)
    cac_q2 = spend_q2 / len(q2_customers)
    ltv_q1 = CanonicalProfitabilityScenarioInjector._cohort_90_day_ltv(
        dataset.orders, customers, q1_customers
    )
    ltv_q2 = CanonicalProfitabilityScenarioInjector._cohort_90_day_ltv(
        dataset.orders, customers, q2_customers
    )
    changes = {
        "conversion_rate": conversion_q2 / conversion_q1 - 1.0,
        "acquired_customers": len(q2_customers) / len(q1_customers) - 1.0,
        "marketing_spend": spend_q2 / spend_q1 - 1.0,
        "cac": cac_q2 / cac_q1 - 1.0,
        "ltv": ltv_q2 / ltv_q1 - 1.0,
    }
    return tuple(
        MetricComparison(
            metric_key=metric.metric_key,
            dimensions=metric.dimensions,
            baseline_period=metric.baseline_period,
            comparison_period=metric.comparison_period,
            comparison_type=metric.comparison_type,
            value=changes[metric.metric_key],
            unit=metric.value_unit,
            evidence_refs=[f"generated-ground-truth:{metric.id}"],
            definition_context=metric.definition_context,
        )
        for metric in CANONICAL_PROFITABILITY_SCENARIO.ground_truth
    )
