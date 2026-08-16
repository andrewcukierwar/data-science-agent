"""Unit tests for the deterministic synthetic ecommerce generator."""

from pathlib import Path

import numpy as np
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
        "session_date",
        "channel",
        "device",
        "converted",
        "customer_id",
    ]
    assert list(dataset.marketing_spend.columns) == [
        "date",
        "channel",
        "spend",
        "impressions",
        "clicks",
    ]


def test_customer_relationships_and_dates_are_valid() -> None:
    config = _small_config()
    dataset = SyntheticEcommerceGenerator(config).generate()
    customers = dataset.customers.set_index("customer_id")

    assert set(dataset.orders["customer_id"]).issubset(customers.index)
    assert (
        dataset.orders["order_date"]
        >= dataset.orders["customer_id"].map(customers["acquisition_date"])
    ).all()
    assert (
        dataset.orders["order_date"]
        <= config.start_date + pd.Timedelta(days=config.period_days - 1)
    ).all()
    converted = dataset.sessions["converted"].astype(bool)
    converted_sessions = dataset.sessions.loc[converted].set_index("customer_id")
    assert len(converted_sessions) == len(dataset.customers)
    assert set(converted_sessions.index) == set(customers.index)
    assert converted_sessions["session_date"].eq(customers["acquisition_date"]).all()
    assert converted_sessions["channel"].eq(customers["acquisition_channel"]).all()
    assert converted_sessions["device"].eq(customers["device"]).all()
    assert dataset.sessions.loc[~converted, "customer_id"].isna().all()
    assert dataset.sessions["session_date"].notna().all()
    assert (dataset.orders["net_revenue"] >= 0).all()
    assert (dataset.orders["cogs"] >= 0).all()
    assert (dataset.orders["cogs"] < dataset.orders["net_revenue"]).all()
    assert (
        dataset.orders["gross_revenue"]
        >= dataset.orders["discount"] + dataset.orders["refund"]
    ).all()
    assert (
        dataset.marketing_spend["clicks"] <= dataset.marketing_spend["impressions"]
    ).all()
    assert (
        (
            dataset.orders["gross_revenue"]
            - dataset.orders["discount"]
            - dataset.orders["refund"]
        )
        .round(2)
        .eq(dataset.orders["net_revenue"])
        .all()
    )


def test_order_economics_vary_across_the_exact_canonical_fixture() -> None:
    config = SyntheticEcommerceConfig(
        seed=42,
        num_customers=1_000,
        num_orders=4_000,
        num_sessions=8_000,
        num_products=4,
        period_days=365,
    )
    orders = SyntheticEcommerceGenerator(config).generate().orders
    customer_order_counts = orders.groupby("customer_id").size()

    assert customer_order_counts.max() - customer_order_counts.min() <= 1
    assert orders["order_date"].nunique() > 100
    assert orders["product_id"].nunique() == config.num_products
    assert orders["quantity"].nunique() >= 3
    assert orders["gross_revenue"].nunique() > 100
    assert orders["discount"].nunique() > 50
    assert orders["refund"].gt(0).any()
    assert (orders["cogs"] / orders["net_revenue"]).round(3).nunique() > 100


def test_converted_acquisition_sessions_reconcile_by_period_and_channel() -> None:
    dataset = SyntheticEcommerceGenerator(_small_config()).generate()
    customers = dataset.customers.assign(
        period=pd.to_datetime(dataset.customers["acquisition_date"]).dt.to_period("D")
    )
    sessions = dataset.sessions.loc[dataset.sessions["converted"]].assign(
        period=pd.to_datetime(
            dataset.sessions.loc[dataset.sessions["converted"], "session_date"]
        ).dt.to_period("D")
    )
    customer_counts = customers.groupby(["period", "acquisition_channel"]).size()
    session_counts = sessions.groupby(["period", "channel"]).size()
    session_counts.index = session_counts.index.set_names(
        ["period", "acquisition_channel"]
    )
    pd.testing.assert_series_equal(
        customer_counts.sort_index(),
        session_counts.sort_index(),
        check_names=True,
    )


def test_session_count_must_cover_all_acquired_customers() -> None:
    with pytest.raises(ValueError, match="num_sessions"):
        SyntheticEcommerceConfig(num_customers=10, num_sessions=9)


def test_marketing_spend_scales_with_configured_company_size() -> None:
    small = SyntheticEcommerceGenerator(
        SyntheticEcommerceConfig(num_customers=1_000)
    )._generate_marketing_spend(np.random.default_rng(123))
    reference = SyntheticEcommerceGenerator(
        SyntheticEcommerceConfig(num_customers=50_000)
    )._generate_marketing_spend(np.random.default_rng(123))

    ratio = reference["spend"].sum() / small["spend"].sum()
    assert ratio == pytest.approx(50.0, rel=1e-6)
    assert small["spend"].sum() / 1_000 < 1_000
    assert 100 < reference["spend"].sum() / 50_000 < 300


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
