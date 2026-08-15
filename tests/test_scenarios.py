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


def _q2_mask(values: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(values):
        values = values.dt.date
    return values.between(date(2025, 4, 1), date(2025, 6, 30))


def _meta_q2_customer_ids(dataset) -> set[str]:
    mask = _q2_mask(dataset.customers["acquisition_date"])
    mask &= dataset.customers["acquisition_channel"].eq("Meta")
    return set(dataset.customers.loc[mask, "customer_id"])


def _meta_q2_conversion_rate(dataset) -> float:
    mask = _q2_mask(dataset.sessions["session_date"])
    mask &= dataset.sessions["channel"].eq("Meta")
    return float(dataset.sessions.loc[mask, "converted"].mean())


def _meta_q2_spend(dataset) -> float:
    mask = _q2_mask(dataset.marketing_spend["date"])
    mask &= dataset.marketing_spend["channel"].eq("Meta")
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


def _reporting_contribution_profit(dataset) -> float:
    customer_ids = _meta_q2_customer_ids(dataset)
    cohort_orders = dataset.orders[dataset.orders["customer_id"].isin(customer_ids)]
    return float(
        cohort_orders["net_revenue"].sum()
        - cohort_orders["cogs"].sum()
        - _meta_q2_spend(dataset)
    )


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

    baseline_q2_meta_ids = _meta_q2_customer_ids(baseline)
    scenario_q2_meta_ids = _meta_q2_customer_ids(scenario)
    removed_ids = baseline_q2_meta_ids - scenario_q2_meta_ids

    assert len(removed_ids) / len(baseline_q2_meta_ids) == pytest.approx(0.18, abs=0.01)
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


def test_canonical_ground_truth_is_observable_across_tables() -> None:
    config = _dataset_config()
    baseline = SyntheticEcommerceGenerator(config).generate()
    scenario = inject_canonical_profitability_scenario(
        baseline,
        CanonicalScenarioInjectionConfig.for_dataset_config(config),
    )

    baseline_customers = _meta_q2_customer_ids(baseline)
    scenario_customers = _meta_q2_customer_ids(scenario)
    customer_change = len(scenario_customers) / len(baseline_customers) - 1
    conversion_change = (
        _meta_q2_conversion_rate(scenario) / _meta_q2_conversion_rate(baseline) - 1
    )
    spend_change = _meta_q2_spend(scenario) / _meta_q2_spend(baseline) - 1
    baseline_cac = _meta_q2_spend(baseline) / len(baseline_customers)
    scenario_cac = _meta_q2_spend(scenario) / len(scenario_customers)
    cac_change = scenario_cac / baseline_cac - 1
    ltv_change = (
        _cohort_90_day_ltv(scenario, scenario_customers)
        / _cohort_90_day_ltv(baseline, baseline_customers)
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
    assert _reporting_contribution_profit(scenario) < _reporting_contribution_profit(
        baseline
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
