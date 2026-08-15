"""Deterministic synthetic ecommerce dataset generation."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

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

    def write(
        self,
        output_dir: str | Path,
        *,
        overwrite: bool = False,
    ) -> dict[str, Path]:
        """Write the generated tables and definitions to a directory."""

        destination = Path(output_dir).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        paths = {
            name: destination / filename for name, filename in self._TABLE_FILES.items()
        }
        existing = [path for path in paths.values() if path.exists()]
        if existing and not overwrite:
            raise FileExistsError(
                "dataset output already exists: "
                + ", ".join(str(path) for path in existing)
            )

        self.customers.to_parquet(paths["customers"], index=False)
        self.orders.to_parquet(paths["orders"], index=False)
        self.sessions.to_parquet(paths["sessions"], index=False)
        self.marketing_spend.to_parquet(paths["marketing_spend"], index=False)
        paths["business_definitions"].write_text(
            self.business_definitions, encoding="utf-8"
        )
        return paths


class SyntheticEcommerceGenerator:
    """Generate one deterministic, scenario-free ecommerce baseline."""

    def __init__(self, config: SyntheticEcommerceConfig | None = None) -> None:
        self.config = config or SyntheticEcommerceConfig()

    def generate(self) -> SyntheticEcommerceDataset:
        """Generate all baseline tables from the configured seed."""

        rng = np.random.default_rng(self.config.seed)
        customers, acquisition_offsets = self._generate_customers(rng)
        orders = self._generate_orders(rng, customers, acquisition_offsets)
        sessions = self._generate_sessions(rng, customers, acquisition_offsets)
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
        acquisition_offsets = rng.integers(
            0, self.config.period_days, size=customer_count
        )
        customers = pd.DataFrame(
            {
                "customer_id": [
                    f"C{index:06d}" for index in range(1, customer_count + 1)
                ],
                "acquisition_date": self._dates_from_offsets(acquisition_offsets),
                "acquisition_channel": rng.choice(
                    self.config.channels,
                    size=customer_count,
                    p=self._channel_probabilities(),
                ),
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
        customer_indices = rng.integers(0, self.config.num_customers, size=order_count)
        order_offsets = self._customer_relative_offsets(
            rng, acquisition_offsets, customer_indices
        )
        product_indices = rng.integers(0, self.config.num_products, size=order_count)
        quantity = rng.integers(1, 4, size=order_count)
        unit_prices = np.linspace(18.0, 180.0, self.config.num_products)
        gross_revenue = np.round(
            unit_prices[product_indices]
            * quantity
            * rng.lognormal(mean=0.0, sigma=0.08, size=order_count),
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
                discounted_revenue * rng.uniform(0.25, 1.0, size=order_count),
                0.0,
            ),
            2,
        )
        refund = np.minimum(refund, discounted_revenue)
        net_revenue = np.round(discounted_revenue - refund, 2)
        cogs = net_revenue * rng.uniform(0.28, 0.58, size=order_count)

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
        acquisition_offsets: np.ndarray,
    ) -> pd.DataFrame:
        session_count = self.config.num_sessions
        customer_indices = rng.integers(
            0, self.config.num_customers, size=session_count
        )
        session_offsets = self._customer_relative_offsets(
            rng, acquisition_offsets, customer_indices
        )
        channels = rng.choice(
            self.config.channels,
            size=session_count,
            p=self._channel_probabilities(),
        )
        conversion_rates = np.linspace(0.075, 0.035, len(self.config.channels))
        channel_positions = {
            channel: position for position, channel in enumerate(self.config.channels)
        }
        converted = rng.random(session_count) < np.array(
            [conversion_rates[channel_positions[channel]] for channel in channels]
        )
        return pd.DataFrame(
            {
                "session_id": [
                    f"S{index:08d}" for index in range(1, session_count + 1)
                ],
                "customer_id": customers.iloc[customer_indices][
                    "customer_id"
                ].to_numpy(),
                "session_date": self._dates_from_offsets(
                    acquisition_offsets[customer_indices] + session_offsets
                ),
                "channel": channels,
                "device": customers.iloc[customer_indices]["device"].to_numpy(),
                "converted": converted,
            }
        )

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

    def _customer_relative_offsets(
        self,
        rng: np.random.Generator,
        acquisition_offsets: np.ndarray,
        customer_indices: np.ndarray,
    ) -> np.ndarray:
        maximum_offsets = (
            self.config.period_days - 1 - acquisition_offsets[customer_indices]
        )
        return np.floor(
            rng.random(len(customer_indices)) * (maximum_offsets + 1)
        ).astype(np.int64)

    def _dates_from_offsets(self, offsets: np.ndarray) -> pd.Series:
        return pd.Series(
            pd.to_datetime(self.config.start_date) + pd.to_timedelta(offsets, unit="D")
        ).dt.date

    def _channel_probabilities(self) -> np.ndarray:
        weights = np.linspace(1.0, 0.5, len(self.config.channels))
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
- **Contribution profit**: `net_revenue - cogs`. Marketing spend is reported
  separately and is not allocated to individual orders in the baseline.
- **30-day LTV**, **60-day LTV**, and **90-day LTV**: cumulative net revenue
  per acquired customer from acquisition through the respective number of
  days after `acquisition_date`.
- **Conversion**: converted sessions divided by total sessions for the same
  reporting slice.

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
