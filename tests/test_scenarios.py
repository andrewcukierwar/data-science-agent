"""Tests for the deterministic canonical profitability scenario."""

from datetime import date

import pandas as pd
import pytest

from scenarios.definitions import CANONICAL_PROFITABILITY_SCENARIO
from scenarios.generator import SyntheticEcommerceConfig, SyntheticEcommerceGenerator
from scenarios.injection import (
    CanonicalScenarioInjectionConfig,
    generate_canonical_profitability_scenario,
    inject_canonical_profitability_scenario,
)


def _dataset_config(seed: int = 42) -> SyntheticEcommerceConfig:
    return SyntheticEcommerceConfig(
        seed=seed,
        num_customers=5_000,
        num_orders=25_000,
        num_sessions=50_000,
        num_products=4,
        period_days=365,
    )


def _canonical_live_config() -> SyntheticEcommerceConfig:
    return SyntheticEcommerceConfig(
        seed=42,
        num_customers=1_000,
        num_orders=4_000,
        num_sessions=8_000,
        num_products=4,
        period_days=365,
    )


def _q2_mask(values: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(values):
        values = values.dt.date
    return values.between(date(2025, 4, 1), date(2025, 6, 30))


def _period_mask(values: pd.Series, period: int) -> pd.Series:
    return pd.to_datetime(values).dt.quarter.eq(period)


def _acquired_customer_ids(dataset, period: int, channel: str = "Meta") -> set[str]:
    mask = _period_mask(dataset.customers["acquisition_date"], period)
    mask &= dataset.customers["acquisition_channel"].eq(channel)
    return set(dataset.customers.loc[mask, "customer_id"])


def _conversion_rate(dataset, period: int, channel: str = "Meta") -> float:
    mask = _period_mask(dataset.sessions["session_date"], period)
    mask &= dataset.sessions["channel"].eq(channel)
    return float(dataset.sessions.loc[mask, "converted"].mean())


def _spend(dataset, period: int, channel: str = "Meta") -> float:
    mask = _period_mask(dataset.marketing_spend["date"], period)
    mask &= dataset.marketing_spend["channel"].eq(channel)
    return float(dataset.marketing_spend.loc[mask, "spend"].sum())


def _cohort_90_day_ltv(dataset, customer_ids: set[str]) -> float:
    customers = dataset.customers.set_index("customer_id")
    acquisition_dates = pd.to_datetime(
        customers.loc[list(customer_ids), "acquisition_date"]
    )
    orders = dataset.orders[dataset.orders["customer_id"].isin(customer_ids)].copy()
    order_age = pd.to_datetime(orders["order_date"]) - orders["customer_id"].map(
        acquisition_dates
    )
    in_window = order_age.between(pd.Timedelta(0), pd.Timedelta(days=90))
    revenue = orders.loc[in_window].groupby("customer_id")["net_revenue"].sum()
    return float(revenue.reindex(customer_ids, fill_value=0.0).mean())


def _reporting_contribution_profit(
    dataset,
    period: int,
    channel: str | None = None,
) -> float:
    if channel is None:
        channels = dataset.customers["acquisition_channel"].unique()
        return sum(
            _reporting_contribution_profit(dataset, period, item) for item in channels
        )
    customer_ids = _acquired_customer_ids(dataset, period, channel)
    cohort_orders = dataset.orders[dataset.orders["customer_id"].isin(customer_ids)]
    return float(
        cohort_orders["net_revenue"].sum()
        - cohort_orders["cogs"].sum()
        - _spend(dataset, period, channel)
    )


def _profit_by_channel(dataset, period: int) -> pd.Series:
    channels = sorted(dataset.customers["acquisition_channel"].unique())
    return pd.Series(
        {
            channel: _reporting_contribution_profit(dataset, period, channel)
            for channel in channels
        }
    )


def _margin(dataset, period: int) -> float:
    orders = dataset.orders.loc[_period_mask(dataset.orders["order_date"], period)]
    return float(1.0 - orders["cogs"].sum() / orders["net_revenue"].sum())


def test_canonical_scenario_definition_contains_typed_ground_truth() -> None:
    definition = CANONICAL_PROFITABILITY_SCENARIO

    assert definition.scenario_id == "canonical-q2-profitability"
    assert "profitability decline" in definition.user_question
    assert "conversion" in definition.expected_primary_driver.lower()
    assert any("LTV" in item for item in definition.known_non_drivers)
    assert any(
        "missing" in item.lower() for item in definition.expected_data_quality_findings
    )
    assert {metric.id for metric in definition.ground_truth} == {
        "meta-q2-conversion-rate",
        "meta-q2-acquired-customers",
        "meta-q2-spend",
        "meta-q2-cac",
        "meta-q2-90-day-ltv",
    }
    assert all(
        metric.comparison.endswith("_vs_q1") for metric in definition.ground_truth
    )


def test_injection_is_reproducible_and_does_not_mutate_clean_baseline() -> None:
    config = _dataset_config()
    baseline = SyntheticEcommerceGenerator(config).generate()
    baseline_snapshot = baseline.customers.copy(deep=True)
    injection_config = CanonicalScenarioInjectionConfig.for_dataset_config(config)

    first = inject_canonical_profitability_scenario(baseline, injection_config)
    second = inject_canonical_profitability_scenario(baseline, injection_config)

    pd.testing.assert_frame_equal(first.customers, second.customers)
    pd.testing.assert_frame_equal(first.orders, second.orders)
    pd.testing.assert_frame_equal(first.sessions, second.sessions)
    pd.testing.assert_frame_equal(first.marketing_spend, second.marketing_spend)
    assert first.business_definitions == second.business_definitions
    pd.testing.assert_frame_equal(baseline.customers, baseline_snapshot)
    assert baseline.business_definitions != first.business_definitions


def test_canonical_scenario_removes_customers_and_associated_rows() -> None:
    baseline = SyntheticEcommerceGenerator(_dataset_config()).generate()
    scenario = generate_canonical_profitability_scenario(_dataset_config()).dataset

    baseline_q2_meta_ids = _acquired_customer_ids(baseline, 2)
    baseline_q1_meta_ids = _acquired_customer_ids(baseline, 1)
    scenario_q2_meta_ids = _acquired_customer_ids(scenario, 2)
    removed_ids = baseline_q2_meta_ids - scenario_q2_meta_ids

    assert len(scenario_q2_meta_ids) / len(baseline_q1_meta_ids) == pytest.approx(
        0.82, abs=0.01
    )
    assert removed_ids.isdisjoint(set(scenario.customers["customer_id"]))
    assert not removed_ids.intersection(set(scenario.orders["customer_id"]))
    assert not removed_ids.intersection(set(scenario.sessions["customer_id"]))
    q2_meta_session_mask = _q2_mask(
        baseline.sessions["session_date"]
    ) & baseline.sessions["channel"].eq("Meta")
    q2_meta_session_customer_ids = set(
        baseline.sessions.loc[q2_meta_session_mask, "customer_id"]
    )
    assert removed_ids <= q2_meta_session_customer_ids

    surviving_ids = baseline_q2_meta_ids - removed_ids
    baseline_survivors = baseline.customers.set_index("customer_id").loc[
        sorted(surviving_ids), "acquisition_channel"
    ]
    scenario_survivors = scenario.customers.set_index("customer_id").loc[
        sorted(surviving_ids), "acquisition_channel"
    ]
    pd.testing.assert_series_equal(baseline_survivors, scenario_survivors)


def test_canonical_ground_truth_is_observable_from_q1_and_q2() -> None:
    scenario = generate_canonical_profitability_scenario(_dataset_config()).dataset

    q1_customers = _acquired_customer_ids(scenario, 1)
    q2_customers = _acquired_customer_ids(scenario, 2)
    customer_change = len(q2_customers) / len(q1_customers) - 1
    conversion_change = (
        _conversion_rate(scenario, 2) / _conversion_rate(scenario, 1) - 1
    )
    spend_change = _spend(scenario, 2) / _spend(scenario, 1) - 1
    q1_cac = _spend(scenario, 1) / len(q1_customers)
    q2_cac = _spend(scenario, 2) / len(q2_customers)
    cac_change = q2_cac / q1_cac - 1
    ltv_change = (
        _cohort_90_day_ltv(scenario, q2_customers)
        / _cohort_90_day_ltv(scenario, q1_customers)
        - 1
    )

    expected = {
        metric.id: metric for metric in CANONICAL_PROFITABILITY_SCENARIO.ground_truth
    }
    assert customer_change == pytest.approx(
        expected["meta-q2-acquired-customers"].expected_relative_change,
        abs=expected["meta-q2-acquired-customers"].tolerance,
    )
    assert conversion_change == pytest.approx(
        expected["meta-q2-conversion-rate"].expected_relative_change,
        abs=expected["meta-q2-conversion-rate"].tolerance,
    )
    assert spend_change == pytest.approx(
        expected["meta-q2-spend"].expected_relative_change,
        abs=expected["meta-q2-spend"].tolerance,
    )
    assert cac_change == pytest.approx(
        expected["meta-q2-cac"].expected_relative_change,
        abs=expected["meta-q2-cac"].tolerance,
    )
    assert ltv_change == pytest.approx(
        expected["meta-q2-90-day-ltv"].expected_relative_change,
        abs=expected["meta-q2-90-day-ltv"].tolerance,
    )
    assert _reporting_contribution_profit(scenario, 2) < _reporting_contribution_profit(
        scenario, 1
    )


def test_exact_canonical_live_configuration_has_a_meta_root_cause() -> None:
    scenario = generate_canonical_profitability_scenario(
        _canonical_live_config()
    ).dataset
    q1_profit = _reporting_contribution_profit(scenario, 1)
    q2_profit = _reporting_contribution_profit(scenario, 2)
    q1_by_channel = _profit_by_channel(scenario, 1)
    q2_by_channel = _profit_by_channel(scenario, 2)
    profit_change = q2_by_channel - q1_by_channel
    overall_decline = q1_profit - q2_profit
    meta_decline = q1_by_channel["Meta"] - q2_by_channel["Meta"]
    non_meta_change = profit_change.drop("Meta")

    q1_customers = _acquired_customer_ids(scenario, 1)
    q2_customers = _acquired_customer_ids(scenario, 2)
    conversion_change = (
        _conversion_rate(scenario, 2) / _conversion_rate(scenario, 1) - 1
    )
    customer_change = len(q2_customers) / len(q1_customers) - 1
    spend_change = _spend(scenario, 2) / _spend(scenario, 1) - 1
    cac_change = (_spend(scenario, 2) / len(q2_customers)) / (
        _spend(scenario, 1) / len(q1_customers)
    ) - 1
    ltv_change = (
        _cohort_90_day_ltv(scenario, q2_customers)
        / _cohort_90_day_ltv(scenario, q1_customers)
        - 1
    )

    assert q2_profit < q1_profit
    assert meta_decline > overall_decline * 0.5
    assert meta_decline > -non_meta_change.min()
    assert conversion_change == pytest.approx(-0.18, abs=0.03)
    assert customer_change == pytest.approx(-0.18, abs=0.01)
    assert spend_change == pytest.approx(0.07, abs=0.002)
    assert cac_change == pytest.approx(0.30, abs=0.03)
    assert ltv_change == pytest.approx(0.0, abs=0.01)
    assert abs(_margin(scenario, 2) - _margin(scenario, 1)) < 0.03
    assert scenario.customers["customer_id"].is_unique
    assert scenario.orders["order_id"].is_unique
    assert scenario.sessions["session_id"].is_unique
    assert scenario.customers.notna().all().all()
    assert scenario.orders.notna().all().all()
    assert scenario.sessions.notna().all().all()
    assert scenario.marketing_spend.notna().all().all()
    assert set(scenario.orders["customer_id"]).issubset(
        set(scenario.customers["customer_id"])
    )
    assert set(scenario.sessions["customer_id"]).issubset(
        set(scenario.customers["customer_id"])
    )
    assert (
        (
            scenario.orders["gross_revenue"]
            - scenario.orders["discount"]
            - scenario.orders["refund"]
        )
        .round(2)
        .eq(scenario.orders["net_revenue"])
        .all()
    )


def test_canonical_scenario_preserves_data_quality_and_order_coherence() -> None:
    scenario = generate_canonical_profitability_scenario(_dataset_config()).dataset

    assert scenario.customers["customer_id"].is_unique
    assert scenario.orders["order_id"].is_unique
    assert scenario.sessions["session_id"].is_unique
    assert scenario.customers.notna().all().all()
    assert scenario.orders.notna().all().all()
    assert scenario.sessions.notna().all().all()
    assert scenario.marketing_spend.notna().all().all()
    assert set(scenario.orders["customer_id"]).issubset(
        set(scenario.customers["customer_id"])
    )
    assert set(scenario.sessions["customer_id"]).issubset(
        set(scenario.customers["customer_id"])
    )
    assert scenario.marketing_spend["date"].nunique() == 365
    assert "SUM(net_revenue) - SUM(cogs) - SUM(marketing_spend)" in (
        scenario.business_definitions
    )
    assert "90-day LTV" in scenario.business_definitions
    assert "missing values" not in scenario.business_definitions


def test_scenario_requires_channels_used_by_the_injection() -> None:
    config = _dataset_config()
    baseline = SyntheticEcommerceGenerator(config).generate()
    baseline.customers["acquisition_channel"] = "Organic"

    with pytest.raises(ValueError, match="Meta"):
        inject_canonical_profitability_scenario(baseline)


def test_scenario_requires_a_q2_window_in_the_baseline() -> None:
    config = SyntheticEcommerceConfig(
        seed=42,
        num_customers=100,
        num_orders=200,
        num_sessions=400,
        num_products=4,
        period_days=30,
    )
    baseline = SyntheticEcommerceGenerator(config).generate()

    with pytest.raises(ValueError, match="Q2"):
        inject_canonical_profitability_scenario(baseline)
