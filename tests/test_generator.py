"""Unit tests for the deterministic synthetic ecommerce generator."""

from pathlib import Path

import pandas as pd
import pytest

from scenarios.generator import (
    SyntheticEcommerceConfig,
    SyntheticEcommerceGenerator,
)


def _small_config(seed: int = 42) -> SyntheticEcommerceConfig:
    return SyntheticEcommerceConfig(
        seed=seed,
        num_customers=24,
        num_orders=80,
        num_sessions=160,
        num_products=4,
        period_days=30,
    )


def test_same_seed_produces_identical_tables_and_definitions() -> None:
    first = SyntheticEcommerceGenerator(_small_config()).generate()
    second = SyntheticEcommerceGenerator(_small_config()).generate()

    pd.testing.assert_frame_equal(first.customers, second.customers)
    pd.testing.assert_frame_equal(first.orders, second.orders)
    pd.testing.assert_frame_equal(first.sessions, second.sessions)
    pd.testing.assert_frame_equal(first.marketing_spend, second.marketing_spend)
    assert first.business_definitions == second.business_definitions


def test_seed_changes_generated_baseline() -> None:
    first = SyntheticEcommerceGenerator(_small_config(seed=1)).generate()
    second = SyntheticEcommerceGenerator(_small_config(seed=2)).generate()

    assert not first.customers.equals(second.customers)


def test_configured_sizes_and_canonical_columns_are_respected() -> None:
    config = _small_config()
    dataset = SyntheticEcommerceGenerator(config).generate()

    assert len(dataset.customers) == config.num_customers
    assert len(dataset.orders) == config.num_orders
    assert len(dataset.sessions) == config.num_sessions
    assert len(dataset.marketing_spend) == config.period_days * len(config.channels)
    assert list(dataset.customers.columns) == [
        "customer_id",
        "acquisition_date",
        "acquisition_channel",
        "region",
        "device",
    ]
    assert list(dataset.orders.columns) == [
        "order_id",
        "customer_id",
        "order_date",
        "product_id",
        "quantity",
        "gross_revenue",
        "discount",
        "refund",
        "net_revenue",
        "cogs",
    ]
    assert list(dataset.sessions.columns) == [
        "session_id",
        "customer_id",
        "session_date",
        "channel",
        "device",
        "converted",
    ]
    assert list(dataset.marketing_spend.columns) == [
        "date",
        "channel",
        "spend",
        "impressions",
        "clicks",
    ]


def test_customer_relationships_and_dates_are_valid() -> None:
    dataset = SyntheticEcommerceGenerator(_small_config()).generate()
    acquisition_dates = dataset.customers.set_index("customer_id")["acquisition_date"]

    assert set(dataset.orders["customer_id"]).issubset(acquisition_dates.index)
    assert set(dataset.sessions["customer_id"]).issubset(acquisition_dates.index)
    assert (
        dataset.orders["order_date"]
        >= dataset.orders["customer_id"].map(acquisition_dates)
    ).all()
    assert (
        dataset.sessions["session_date"]
        >= dataset.sessions["customer_id"].map(acquisition_dates)
    ).all()
    assert (dataset.orders["net_revenue"] >= 0).all()
    assert (dataset.orders["cogs"] >= 0).all()
    assert (
        dataset.marketing_spend["clicks"] <= dataset.marketing_spend["impressions"]
    ).all()


def test_write_outputs_parquet_tables_and_documentation(tmp_path: Path) -> None:
    dataset = SyntheticEcommerceGenerator(_small_config()).generate()
    output_dir = tmp_path / "ecommerce"

    paths = dataset.write(output_dir)

    assert set(paths) == {
        "customers",
        "orders",
        "sessions",
        "marketing_spend",
        "business_definitions",
    }
    assert pd.read_parquet(paths["customers"]).equals(dataset.customers)
    assert pd.read_parquet(paths["orders"]).equals(dataset.orders)
    assert "CAC" in paths["business_definitions"].read_text(encoding="utf-8")

    with pytest.raises(FileExistsError):
        dataset.write(output_dir)

    dataset.write(output_dir, overwrite=True)
