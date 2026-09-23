"""Tests for versioned scenario discovery and generated-source invariants."""

import inspect
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pandas as pd
import pytest

from scenarios import discover_scenarios, get_scenario
from scenarios.catalog import (
    ScenarioCatalog,
    ScenarioCatalogError,
    ScenarioRegistration,
)
from scenarios.definitions import CANONICAL_PROFITABILITY_SCENARIO
from scenarios.definitions.business_root_cause import (
    BUSINESS_ROOT_CAUSE_SCENARIOS,
)
from scenarios.definitions.data_quality import DATA_QUALITY_SCENARIOS
from scenarios.definitions.experiments import EXPERIMENT_SCENARIOS
from scenarios.definitions.mix import CHANNEL_MIX_CONFOUNDING_SCENARIO
from scenarios.generator import SyntheticEcommerceConfig, SyntheticEcommerceGenerator
from scenarios.invariants import (
    BASELINE_ONLY_DOCUMENT_CLAIMS,
    ScenarioInvariantError,
    baseline_only_document_claims,
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
    registration = get_scenario("canonical-q2-profitability", "1.1")
    assert registration.generator_name == "generate_canonical_profitability_scenario"
    # The task and evaluator boundaries are versioned independently.
    assert registration.evaluator_rules().evaluator_version == "1.3"
    assert registration.evaluation_spec.ground_truth == (
        *CANONICAL_PROFITABILITY_SCENARIO.ground_truth,
    )

    with pytest.raises(ScenarioCatalogError, match="unknown scenario registration"):
        get_scenario("canonical-q2-profitability", "9.0")


def test_public_task_periods_match_current_evaluator_scopes() -> None:
    current_definitions = (
        *BUSINESS_ROOT_CAUSE_SCENARIOS,
        CHANNEL_MIX_CONFOUNDING_SCENARIO,
    )
    for definition in current_definitions:
        registration = get_scenario(definition.scenario_id, "1.1")
        question = registration.metadata.user_question.lower()
        for condition in registration.evaluation_spec.injected_conditions:
            assert condition.id.lower() not in question
        injection = definition.generation_config.get("injection", {})
        if isinstance(injection, dict):
            for key, value in injection.items():
                if "channel" in key.lower() and isinstance(value, str):
                    assert value.lower() not in question
        periods = {
            (metric.baseline_period, metric.comparison_period)
            for metric in registration.evaluation_spec.ground_truth
            if metric.baseline_period.startswith("Q")
            and metric.comparison_period.startswith("Q")
        }
        for baseline, comparison in periods:
            assert baseline.lower() in question
            assert comparison.lower() in question
        if any(
            metric.definition_context is not None
            and metric.definition_context.observation_window == "90_day"
            for metric in registration.evaluation_spec.ground_truth
        ):
            assert "90 days" in question

    for definition in DATA_QUALITY_SCENARIOS:
        registration = get_scenario(definition.scenario_id, "1.1")
        required_scope = registration.evaluator_rules().data_quality_policy
        question = registration.metadata.user_question
        required_date_value = required_scope.required_issues[0].scope.date
        assert required_date_value is not None
        if "2025 q2" in question.lower():
            assert required_date_value.year == 2025
            assert required_date_value.month in {4, 5, 6}
        else:
            assert "latest available 2025 reporting period" in question.lower()
            assert required_date_value.year == 2025
        assert all(
            condition.id.lower() not in question.lower()
            for condition in registration.evaluation_spec.injected_conditions
        )
        injection = definition.generation_config.get("injection", {})
        if isinstance(injection, dict):
            for key, value in injection.items():
                if "channel" in key.lower() and isinstance(value, str):
                    assert value.lower() not in question.lower()

    for definition in EXPERIMENT_SCENARIOS:
        registration = get_scenario(definition.scenario_id, "1.1")
        question = registration.metadata.user_question.lower()
        expectation = registration.evaluation_spec.statistical_expectation
        assert expectation is not None
        assert "treatment minus control" in question
        assert "randomly assigned" in question
        assert "experiment enrollment" in question
        assert "pooled two-proportion z test" in question
        assert "unpooled wald confidence interval" in question
        assert "cohen's h" in question
        assert (
            expectation.required_procedure.value
            == "pooled_two_proportion_z_with_unpooled_wald_ci"
        )
        assert expectation.required_effect_size_method.value == "signed_cohen_h"

    # The canonical task already names Q2 and remains textually unchanged.
    assert get_scenario("canonical-q2-profitability", "1.1").metadata.user_question == (
        "Why did profitability decline in Q2, and what should the company do about it?"
    )


def test_model_context_drops_scenario_and_evaluator_sentinel_metadata() -> None:
    from evaluation.contracts import ScenarioMetadata

    sentinel = "HIDDEN_SENTINEL_CAUSE_SCENARIO_7F31"
    metadata = ScenarioMetadata(
        scenario_id=sentinel,
        scenario_version="1.1",
        name=sentinel,
        seed=42,
        user_question="Assess the observed business results.",
        evaluator_version="1.3",
    )

    context = metadata.model_visible_context()
    encoded = context.model_dump_json()
    assert context.user_question == metadata.user_question
    assert sentinel not in encoded
    assert "evaluator_version" not in encoded
    assert "scenario_version" not in encoded


def test_task_versions_preserve_v8_questions_and_all_numerical_truths() -> None:
    scenario_ids = {
        item.scenario_id
        for item in discover_scenarios().registrations
        if item.scenario_version == "1.1"
    }
    for scenario_id in scenario_ids:
        legacy = get_scenario(scenario_id, "1.0")
        current = get_scenario(scenario_id, "1.1")
        legacy_metrics = tuple(
            (
                metric.metric_key,
                metric.dimensions,
                metric.baseline_period,
                metric.comparison_period,
                metric.expected_relative_change,
                metric.tolerance,
            )
            for metric in legacy.evaluation_spec.ground_truth
        )
        current_metrics = tuple(
            (
                metric.metric_key,
                metric.dimensions,
                metric.baseline_period,
                metric.comparison_period,
                metric.expected_relative_change,
                metric.tolerance,
            )
            for metric in current.evaluation_spec.ground_truth
        )
        assert current_metrics == legacy_metrics

        old_expectation = legacy.evaluation_spec.statistical_expectation
        new_expectation = current.evaluation_spec.statistical_expectation
        if old_expectation is not None:
            assert new_expectation is not None
            assert (
                old_expectation.expected_estimate,
                old_expectation.estimate_tolerance,
                old_expectation.expected_confidence_interval,
                old_expectation.confidence_interval_tolerance,
                old_expectation.expected_p_value,
                old_expectation.p_value_tolerance,
                old_expectation.expected_effect_size,
                old_expectation.effect_size_tolerance,
            ) == (
                new_expectation.expected_estimate,
                new_expectation.estimate_tolerance,
                new_expectation.expected_confidence_interval,
                new_expectation.confidence_interval_tolerance,
                new_expectation.expected_p_value,
                new_expectation.p_value_tolerance,
                new_expectation.expected_effect_size,
                new_expectation.effect_size_tolerance,
            )

    assert get_scenario("missing-reporting-day", "1.0").metadata.user_question == (
        "Assess whether the available reporting supports a reliable Q2 business "
        "comparison and describe any limitations."
    )
    assert (
        "2025-05-31"
        not in get_scenario("missing-reporting-day", "1.0").metadata.user_question
    )
    assert (
        "2025 q2"
        in get_scenario("missing-reporting-day", "1.1").metadata.user_question.lower()
    )


def test_catalog_rejects_duplicate_versioned_registration() -> None:
    registration = get_scenario("canonical-q2-profitability", "1.1")

    with pytest.raises(ScenarioCatalogError, match="duplicate"):
        ScenarioCatalog((registration, registration))


def test_canonical_generic_contract_preserves_model_visible_context_and_answer() -> (
    None
):
    registration = get_scenario("canonical-q2-profitability", "1.1")
    legacy_context = CANONICAL_PROFITABILITY_SCENARIO.model_visible_context()
    generic_context = registration.model_context_contract()

    assert (
        registration.model_visible_context.model_dump() == legacy_context.model_dump()
    )
    assert (
        generic_context.user_question == CANONICAL_PROFITABILITY_SCENARIO.user_question
    )
    assert set(generic_context.model_dump()) == {"contract_version", "user_question"}
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
    document = dataset.business_definitions.lower()
    assert "clean baseline" not in document
    assert "no business or data-quality scenario is injected" not in document

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


def _generate_registered_scenario(registration: ScenarioRegistration) -> object:
    """Generate one registered scenario at test scale, whatever its config type."""

    parameters = inspect.signature(registration.generator).parameters
    if "dataset_config" in parameters:
        return registration.generate_validated(_small_config())
    return registration.generate_validated()


@pytest.mark.parametrize(
    "scenario_id",
    sorted(item.scenario_id for item in discover_scenarios()),
)
def test_injected_scenario_documents_do_not_retain_baseline_only_assertions(
    scenario_id: str,
) -> None:
    """No registered scenario document may claim whether an injection occurred.

    The parametrization is derived from the catalog rather than hard-coded, so
    a newly registered scenario cannot silently skip this check.
    """

    registration = get_scenario(scenario_id)
    generated = _generate_registered_scenario(registration)

    assert baseline_only_document_claims(generated.dataset) == ()
    assert not any(
        claim in registration.model_context_contract().model_dump_json().lower()
        for claim in BASELINE_ONLY_DOCUMENT_CLAIMS
    )


def test_every_declared_matrix_scenario_is_covered_by_the_document_regression() -> None:
    """The document regression must cover the full declared benchmark matrix."""

    registrations = discover_scenarios().registrations
    assert len({item.scenario_id for item in registrations}) == 10
    assert len(registrations) == 20


@pytest.mark.parametrize("claim", BASELINE_ONLY_DOCUMENT_CLAIMS)
def test_reintroducing_a_baseline_only_claim_fails_scenario_validation(
    claim: str,
) -> None:
    """A document that reasserts injection status must fail generation."""

    registration = get_scenario("retention-q2-deterioration")
    generated = registration.generate(_small_config())
    document = generated.dataset.business_definitions
    regressed = replace(
        generated.dataset,
        business_definitions=f"{document}\n- This dataset {claim.upper()}.\n",
    )

    assert baseline_only_document_claims(regressed) == (claim,)
    report = registration.invariant_suite.validate(regressed)
    assert not report.passed
    assert any(
        violation.invariant_id == "document:injection-status-claim"
        for violation in report.violations
    )
    with pytest.raises(ScenarioInvariantError, match="injection-status-claim"):
        report.assert_valid()


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
    registration = get_scenario("canonical-q2-profitability")
    generated = registration.generate(_small_config())
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
