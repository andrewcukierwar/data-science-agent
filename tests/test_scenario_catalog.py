"""Tests for versioned scenario discovery and generated-source invariants."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pandas as pd
import pytest

from scenarios import discover_scenarios, get_scenario
from scenarios.catalog import ScenarioCatalog, ScenarioCatalogError
from scenarios.definitions import CANONICAL_PROFITABILITY_SCENARIO
from scenarios.generator import SyntheticEcommerceConfig, SyntheticEcommerceGenerator
from scenarios.injection import generate_canonical_profitability_scenario
from scenarios.invariants import (
    check_metric_identities,
    validate_synthetic_ecommerce_baseline,
)


def _small_config() -> SyntheticEcommerceConfig:
    return SyntheticEcommerceConfig(
        seed=42,
        num_customers=1_000,
        num_orders=4_000,
        num_sessions=8_000,
        num_products=4,
        period_days=365,
    )


def test_catalog_resolves_canonical_generator_evaluator_and_version() -> None:
    catalog = discover_scenarios()
    registrations = catalog.registrations

    assert len(registrations) == len(
        {(item.scenario_id, item.scenario_version) for item in registrations}
    )
    registration = get_scenario("canonical-q2-profitability", "1.0")
    assert registration.generator_name == "generate_canonical_profitability_scenario"
    assert registration.evaluator_rules().evaluator_version == "1.1"
    assert registration.evaluation_spec.ground_truth == (
        *CANONICAL_PROFITABILITY_SCENARIO.ground_truth,
    )

    with pytest.raises(ScenarioCatalogError, match="unknown scenario registration"):
        get_scenario("canonical-q2-profitability", "9.0")


def test_catalog_rejects_duplicate_versioned_registration() -> None:
    registration = get_scenario("canonical-q2-profitability", "1.0")

    with pytest.raises(ScenarioCatalogError, match="duplicate"):
        ScenarioCatalog((registration, registration))


def test_canonical_generic_contract_preserves_model_visible_context_and_answer() -> (
    None
):
    registration = get_scenario("canonical-q2-profitability", "1.0")
    legacy_context = CANONICAL_PROFITABILITY_SCENARIO.model_visible_context()
    generic_context = registration.model_context_contract()

    assert (
        registration.model_visible_context.model_dump() == legacy_context.model_dump()
    )
    assert generic_context.scenario_id == legacy_context.scenario_id
    assert generic_context.scenario_version == legacy_context.scenario_version
    assert generic_context.name == legacy_context.name
    assert (
        generic_context.user_question == CANONICAL_PROFITABILITY_SCENARIO.user_question
    )
    model_text = generic_context.model_dump_json()
    assert all(
        metric.id not in model_text
        for metric in registration.evaluation_spec.ground_truth
    )


def test_clean_baseline_is_independently_validated_and_source_bytes_are_stable(
    tmp_path: Path,
) -> None:
    dataset = SyntheticEcommerceGenerator(_small_config()).generate()
    report = validate_synthetic_ecommerce_baseline(dataset)
    assert report.passed, report.violations

    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    dataset.write(first_dir)
    dataset.write(second_dir)
    first_files = sorted(path.name for path in first_dir.iterdir())
    second_files = sorted(path.name for path in second_dir.iterdir())
    assert first_files == second_files
    assert all(
        sha256((first_dir / name).read_bytes()).digest()
        == sha256((second_dir / name).read_bytes()).digest()
        for name in first_files
    )


def test_common_invariants_reject_keys_dates_nulls_and_economic_identities() -> None:
    dataset = SyntheticEcommerceGenerator(_small_config()).generate()

    duplicate_key = replace(dataset, customers=dataset.customers.copy())
    duplicate_key.customers.loc[1, "customer_id"] = duplicate_key.customers.loc[
        0, "customer_id"
    ]
    key_report = validate_synthetic_ecommerce_baseline(duplicate_key)
    assert any(
        item.invariant_id.startswith("key:customers") for item in key_report.violations
    )

    bad_date = replace(dataset, orders=dataset.orders.copy())
    bad_date.orders.loc[0, "order_date"] = "not-a-date"
    date_report = validate_synthetic_ecommerce_baseline(bad_date)
    assert any(
        item.invariant_id.startswith("date:orders") for item in date_report.violations
    )

    undocumented_null = replace(dataset, sessions=dataset.sessions.copy())
    converted_index = undocumented_null.sessions.index[
        undocumented_null.sessions["converted"].astype(bool)
    ][0]
    undocumented_null.sessions.loc[converted_index, "customer_id"] = None
    null_report = validate_synthetic_ecommerce_baseline(undocumented_null)
    assert any(
        item.invariant_id.startswith("documented_null:sessions")
        for item in null_report.violations
    )

    broken_economics = replace(dataset, orders=dataset.orders.copy())
    broken_economics.orders.loc[0, "net_revenue"] += 1.0
    economics_report = validate_synthetic_ecommerce_baseline(broken_economics)
    assert any(
        item.invariant_id == "economic:orders.net_revenue"
        for item in economics_report.violations
    )


def test_canonical_observable_ground_truth_is_registered_as_an_invariant() -> None:
    registration = get_scenario("canonical-q2-profitability", "1.0")
    generated = generate_canonical_profitability_scenario(_small_config())
    report = registration.validate_generated(generated)
    assert report.passed, report.violations

    changed_spend = replace(
        generated.dataset, marketing_spend=generated.dataset.marketing_spend.copy()
    )
    q2_meta = pd.to_datetime(changed_spend.marketing_spend["date"]).dt.quarter.eq(
        2
    ) & changed_spend.marketing_spend["channel"].eq("Meta")
    changed_spend.marketing_spend.loc[q2_meta, "spend"] *= 1.2
    changed_report = registration.invariant_suite.validate(changed_spend)
    assert any(
        item.invariant_id == "ground_truth:meta-q2-spend"
        for item in changed_report.violations
    )


def test_metric_identity_checks_reject_duplicate_estimands() -> None:
    metrics = CANONICAL_PROFITABILITY_SCENARIO.ground_truth
    duplicate = metrics[0].model_copy(update={"id": "duplicate-id"})
    violations = check_metric_identities((metrics[0], duplicate))
    assert any(item.invariant_id == "metric_identity:estimands" for item in violations)
