"""Deterministic synthetic ecommerce dataset generation."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from scenarios.sources import write_deterministic_sources

DEFAULT_CHANNELS = (
    "Meta",
    "Google",
    "Email",
    "Affiliate",
    "Organic",
)
REFERENCE_CUSTOMER_COUNT = 50_000
REGIONS = ("Northeast", "South", "Midwest", "West")
DEVICES = ("mobile", "desktop", "tablet")


class SyntheticEcommerceConfig(BaseModel):
    """Seed and dataset dimensions for one clean synthetic company."""

    model_config = ConfigDict(extra="forbid")

    seed: int = Field(default=42, ge=0)
    num_customers: int = Field(default=50_000, ge=1)
    num_orders: int = Field(default=240_000, ge=1)
    num_sessions: int = Field(default=1_000_000, ge=1)
    num_products: int = Field(default=20, ge=1)
    period_days: int = Field(default=365, ge=1)
    start_date: date = date(2025, 1, 1)
    channels: tuple[str, ...] = Field(default=DEFAULT_CHANNELS, min_length=1)

    @model_validator(mode="after")
    def channels_are_unique_and_non_empty(self) -> "SyntheticEcommerceConfig":
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("channels must be unique")
        if any(not channel.strip() for channel in self.channels):
            raise ValueError("channels must not be empty")
        if self.num_sessions < self.num_customers:
            raise ValueError(
                "num_sessions must be at least num_customers so every acquired "
                "customer can have one converted acquisition session"
            )
        return self


@dataclass(frozen=True, slots=True)
class SyntheticEcommerceDataset:
    """Generated tables and business definitions for one synthetic company."""

    customers: pd.DataFrame
    orders: pd.DataFrame
    sessions: pd.DataFrame
    marketing_spend: pd.DataFrame
    business_definitions: str

    _TABLE_FILES: ClassVar[dict[str, str]] = {
        "customers": "customers.parquet",
        "orders": "orders.parquet",
        "sessions": "sessions.parquet",
        "marketing_spend": "marketing_spend.parquet",
        "business_definitions": "business_definitions.md",
    }

    def table_map(self) -> dict[str, pd.DataFrame]:
        """Return the generated tables keyed by their logical source names."""

        return {
            "customers": self.customers,
            "orders": self.orders,
            "sessions": self.sessions,
            "marketing_spend": self.marketing_spend,
        }

    def write(
        self,
        output_dir: str | Path,
        *,
        overwrite: bool = False,
    ) -> dict[str, Path]:
        """Write the generated tables and definitions to a directory."""

        return write_deterministic_sources(
            output_dir,
            self.table_map(),
            {"business_definitions": self.business_definitions},
            table_filenames={
                name: filename
                for name, filename in self._TABLE_FILES.items()
                if name != "business_definitions"
            },
            document_filenames={
                "business_definitions": self._TABLE_FILES["business_definitions"]
            },
            overwrite=overwrite,
        )


class SyntheticEcommerceGenerator:
    """Generate one deterministic, scenario-free ecommerce baseline."""

    def __init__(self, config: SyntheticEcommerceConfig | None = None) -> None:
        self.config = config or SyntheticEcommerceConfig()

    def generate(self) -> SyntheticEcommerceDataset:
        """Generate all baseline tables from the configured seed."""

        rng = np.random.default_rng(self.config.seed)
        customers, acquisition_offsets = self._generate_customers(rng)
        orders = self._generate_orders(rng, customers, acquisition_offsets)
        sessions = self._generate_sessions(rng, customers)
        marketing_spend = self._generate_marketing_spend(rng)
        return SyntheticEcommerceDataset(
            customers=customers,
            orders=orders,
            sessions=sessions,
            marketing_spend=marketing_spend,
            business_definitions=self._business_definitions(),
        )

    def _generate_customers(
        self,
        rng: np.random.Generator,
    ) -> tuple[pd.DataFrame, np.ndarray]:
        customer_count = self.config.num_customers
        acquisition_offsets = np.arange(customer_count) % self.config.period_days
        rng.shuffle(acquisition_offsets)
        acquisition_dates = self._dates_from_offsets(acquisition_offsets)
        acquisition_quarters = pd.to_datetime(acquisition_dates).dt.quarter.to_numpy()
        acquisition_channels = np.empty(customer_count, dtype=object)
        for quarter in np.unique(acquisition_quarters):
            quarter_indices = np.flatnonzero(acquisition_quarters == quarter)
            acquisition_channels[quarter_indices] = self._balanced_channel_choices(
                rng,
                len(quarter_indices),
            )
        customers = pd.DataFrame(
            {
                "customer_id": [
                    f"C{index:06d}" for index in range(1, customer_count + 1)
                ],
                "acquisition_date": acquisition_dates,
                "acquisition_channel": acquisition_channels,
                "region": rng.choice(REGIONS, size=customer_count),
                "device": rng.choice(
                    DEVICES,
                    size=customer_count,
                    p=np.array([0.58, 0.34, 0.08]),
                ),
            }
        )
        return customers, acquisition_offsets

    def _generate_orders(
        self,
        rng: np.random.Generator,
        customers: pd.DataFrame,
        acquisition_offsets: np.ndarray,
    ) -> pd.DataFrame:
        order_count = self.config.num_orders
        customer_count = self.config.num_customers
        cycle_indices = np.arange(order_count) // customer_count
        cycle_count = int(cycle_indices.max()) + 1
        customer_indices = np.arange(order_count) % customer_count
        for cycle in range(cycle_count):
            cycle_mask = cycle_indices == cycle
            cycle_customers = customer_indices[cycle_mask].copy()
            rng.shuffle(cycle_customers)
            customer_indices[cycle_mask] = cycle_customers
        maximum_offsets = (
            self.config.period_days - 1 - acquisition_offsets[customer_indices]
        )
        # Keep the balanced customer assignment above, but draw every order's
        # economics independently. Reusing one realization per cycle makes all
        # customers in a cycle share identical timing, products, discounts, and
        # margins, which collapses customer-level LTV distributions.
        order_offsets = np.minimum(
            np.floor(rng.exponential(scale=60.0, size=order_count)).astype(np.int64),
            maximum_offsets,
        )
        product_indices = rng.integers(
            0,
            self.config.num_products,
            size=order_count,
        )
        quantity = rng.choice(
            np.array([1, 2, 3, 4], dtype=np.int64),
            size=order_count,
            p=np.array([0.52, 0.30, 0.13, 0.05]),
        )
        unit_prices = np.linspace(18.0, 180.0, self.config.num_products)
        gross_revenue = np.round(
            unit_prices[product_indices]
            * quantity
            * rng.lognormal(mean=0.0, sigma=0.12, size=order_count),
            2,
        )
        discount = np.round(
            gross_revenue * rng.beta(2.0, 18.0, size=order_count) * 0.35,
            2,
        )
        discount = np.minimum(discount, gross_revenue)
        discounted_revenue = gross_revenue - discount
        refunded = rng.random(order_count) < 0.03
        refund = np.round(
            np.where(
                refunded,
                discounted_revenue * rng.uniform(0.20, 0.85, size=order_count),
                0.0,
            ),
            2,
        )
        refund = np.minimum(refund, discounted_revenue)
        net_revenue = np.round(discounted_revenue - refund, 2)
        cogs = np.round(net_revenue * rng.uniform(0.28, 0.58, size=order_count), 2)

        return pd.DataFrame(
            {
                "order_id": [f"O{index:07d}" for index in range(1, order_count + 1)],
                "customer_id": customers.iloc[customer_indices][
                    "customer_id"
                ].to_numpy(),
                "order_date": self._dates_from_offsets(
                    acquisition_offsets[customer_indices] + order_offsets
                ),
                "product_id": [f"P{index + 1:03d}" for index in product_indices],
                "quantity": quantity,
                "gross_revenue": gross_revenue,
                "discount": discount,
                "refund": refund,
                "net_revenue": net_revenue,
                "cogs": np.round(cogs, 2),
            }
        )

    def _generate_sessions(
        self,
        rng: np.random.Generator,
        customers: pd.DataFrame,
    ) -> pd.DataFrame:
        """Generate acquisition traffic with a reconciled conversion event.

        Customer creation and conversion are intentionally one operation in the
        baseline: each customer contributes exactly one converted acquisition
        session on their acquisition date. Remaining configured sessions are
        anonymous, non-converting acquisition traffic. This makes the customer
        denominator and funnel numerator observable from the same table.
        """

        customer_count = len(customers)
        anonymous_count = self.config.num_sessions - customer_count
        acquisition_sessions = pd.DataFrame(
            {
                "customer_id": customers["customer_id"].to_numpy(),
                "session_date": customers["acquisition_date"].to_numpy(),
                "channel": customers["acquisition_channel"].to_numpy(),
                "device": customers["device"].to_numpy(),
                "converted": np.ones(customer_count, dtype=bool),
            }
        )

        anonymous_sessions = pd.DataFrame(
            {
                "customer_id": pd.Series([None] * anonymous_count, dtype="object"),
                "session_date": self._dates_from_offsets(
                    rng.integers(
                        0,
                        self.config.period_days,
                        size=anonymous_count,
                    )
                ),
                "channel": rng.choice(
                    self.config.channels,
                    size=anonymous_count,
                    p=self._session_channel_probabilities(),
                ),
                "device": rng.choice(
                    DEVICES,
                    size=anonymous_count,
                    p=np.array([0.58, 0.34, 0.08]),
                ),
                "converted": np.zeros(anonymous_count, dtype=bool),
            }
        )
        sessions = pd.concat(
            [acquisition_sessions, anonymous_sessions],
            ignore_index=True,
        )
        sessions = sessions.iloc[rng.permutation(len(sessions))].reset_index(drop=True)
        sessions.insert(
            0,
            "session_id",
            [f"S{index:08d}" for index in range(1, len(sessions) + 1)],
        )
        return sessions[
            [
                "session_id",
                "session_date",
                "channel",
                "device",
                "converted",
                "customer_id",
            ]
        ]

    def _generate_marketing_spend(
        self,
        rng: np.random.Generator,
    ) -> pd.DataFrame:
        days = pd.date_range(
            self.config.start_date,
            periods=self.config.period_days,
            freq="D",
        )
        day_count = len(days)
        channel_count = len(self.config.channels)
        channel_positions = np.tile(np.arange(channel_count), day_count)
        day_positions = np.repeat(np.arange(day_count), channel_count)
        company_scale = self.config.num_customers / REFERENCE_CUSTOMER_COUNT
        base_spend = np.linspace(9_000.0, 2_000.0, channel_count) * company_scale
        base_cpm = np.linspace(8.0, 18.0, channel_count)
        base_ctr = np.linspace(0.018, 0.045, channel_count)
        seasonal = 1.0 + 0.12 * np.sin(2 * np.pi * day_positions / day_count)
        spend = (
            base_spend[channel_positions]
            * seasonal
            * rng.lognormal(mean=0.0, sigma=0.08, size=len(day_positions))
        )
        impressions = np.maximum(
            1,
            np.round(spend / base_cpm[channel_positions] * 1_000).astype(np.int64),
        )
        clicks = rng.binomial(
            impressions,
            base_ctr[channel_positions],
        )
        return pd.DataFrame(
            {
                "date": days.to_numpy()[day_positions],
                "channel": np.asarray(self.config.channels)[channel_positions],
                "spend": np.round(spend, 2),
                "impressions": impressions,
                "clicks": clicks,
            }
        )

    def _dates_from_offsets(self, offsets: np.ndarray) -> pd.Series:
        return pd.Series(
            pd.to_datetime(self.config.start_date) + pd.to_timedelta(offsets, unit="D")
        ).dt.date

    def _channel_probabilities(self) -> np.ndarray:
        weights = np.linspace(1.0, 0.5, len(self.config.channels))
        return weights / weights.sum()

    def _balanced_channel_choices(
        self,
        rng: np.random.Generator,
        count: int,
    ) -> np.ndarray:
        """Allocate channels evenly according to configured probabilities."""

        expected = self._channel_probabilities() * count
        channel_counts = np.floor(expected).astype(int)
        remainder = count - int(channel_counts.sum())
        if remainder:
            fractional_order = np.argsort(-(expected - channel_counts))
            channel_counts[fractional_order[:remainder]] += 1
        choices = np.repeat(np.asarray(self.config.channels), channel_counts)
        rng.shuffle(choices)
        return choices

    def _session_channel_probabilities(self) -> np.ndarray:
        """Return a distinct but stable mix for anonymous acquisition traffic."""

        weights = np.linspace(1.15, 0.65, len(self.config.channels))
        return weights / weights.sum()

    @staticmethod
    def _business_definitions() -> str:
        return """# Business Definitions

## Metrics

- **CAC**: marketing spend divided by the number of new customers acquired
  through the corresponding channel during the reporting period.
- **New customer**: a customer whose `acquisition_date` falls in the reporting
  period. A customer is counted once, regardless of order count.
- **Gross revenue**: the pre-discount, pre-refund value of items in an order,
  calculated as quantity multiplied by the generated item price.
- **Net revenue**: `gross_revenue - discount - refund`.
- **Contribution profit**: `net_revenue - cogs` at the order level.
- **90-day acquisition-cohort contribution profit**: for customers acquired in
  a reporting period, sum `net_revenue - cogs` for their orders from
  `acquisition_date` through `acquisition_date + 90 days`, then subtract
  marketing spend for the corresponding acquisition period and channel. The
  same 90-day observation window is used for every cohort comparison.
- **30-day LTV**, **60-day LTV**, and **90-day LTV**: cumulative net revenue
  per acquired customer from acquisition through the respective number of
  days after `acquisition_date`.
- **Conversion**: converted acquisition sessions divided by all acquisition
  sessions for the same reporting slice. Every customer has exactly one
  converted acquisition session on `acquisition_date`; additional
  non-converting acquisition sessions intentionally have `customer_id = null`
  because no customer was created.

## Data treatment

- Refunds are deducted from revenue in the order that contains them.
- The baseline does not generate canceled orders. If a canceled-order field is
  added later, canceled orders should be excluded from revenue and order-count
  metrics.
- Dates are calendar dates interpreted in the UTC reporting timezone.
- This dataset is a clean baseline. No business or data-quality scenario is
  injected during generation.
"""


def generate_synthetic_ecommerce(
    config: SyntheticEcommerceConfig | None = None,
) -> SyntheticEcommerceDataset:
    """Convenience wrapper for generating a deterministic baseline."""

    return SyntheticEcommerceGenerator(config).generate()
