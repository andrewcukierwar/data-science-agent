"""Deterministic fixtures for the business root-cause scenario catalog."""

from collections.abc import Callable

import pandas as pd
import pytest

from evaluation.primitives import numeric_ground_truth_failures
from scenarios import get_scenario
from scenarios.business_scenarios import (
    observe_cogs_margin_ground_truth,
    observe_discount_refund_ground_truth,
    observe_retention_ground_truth,
)
from scenarios.generator import SyntheticEcommerceConfig


def _configured_scale() -> SyntheticEcommerceConfig:
    return SyntheticEcommerceConfig(
        seed=42,
        num_customers=1_000,
        num_orders=4_000,
        num_sessions=8_000,
        num_products=4,
        period_days=365,
    )


_SCENARIOS: tuple[tuple[str, Callable], ...] = (
    ("retention-q2-deterioration", observe_retention_ground_truth),
    ("cogs-q2-margin-deterioration", observe_cogs_margin_ground_truth),
    ("discount-refund-q2-deterioration", observe_discount_refund_ground_truth),
)


@pytest.mark.parametrize(("scenario_id", "observer"), _SCENARIOS)
def test_business_scenario_generator_and_observable_ground_truth(
    scenario_id: str,
    observer: Callable,
) -> None:
    registration = get_scenario(scenario_id, "1.0")
    generated = registration.generate_validated(_configured_scale())

    observed = observer(generated.dataset)
    assert (
        numeric_ground_truth_failures(
            observed, registration.evaluation_spec.ground_truth
        )
        == []
    )
    assert registration.invariant_suite.validate(generated.dataset).passed


@pytest.mark.parametrize(("scenario_id", "observer"), _SCENARIOS)
def test_business_scenario_generation_is_byte_reproducible(
    scenario_id: str,
    observer: Callable,
) -> None:
    registration = get_scenario(scenario_id, "1.0")
    first = registration.generate(_configured_scale())
    second = registration.generate(_configured_scale())

    for table_name in first.dataset.table_map():
        pd.testing.assert_frame_equal(
            first.dataset.table_map()[table_name],
            second.dataset.table_map()[table_name],
        )
    assert first.dataset.business_definitions == second.dataset.business_definitions
    assert [item.model_dump_json() for item in observer(first.dataset)] == [
        item.model_dump_json() for item in observer(second.dataset)
    ]


@pytest.mark.parametrize(
    ("scenario_id", "metric_id", "context_update"),
    (
        (
            "retention-q2-deterioration",
            "email-q2-retention-rate",
            {"population": "all customers"},
        ),
        (
            "retention-q2-deterioration",
            "email-q2-retention-rate",
            {"date_basis": "order_date only"},
        ),
        (
            "cogs-q2-margin-deterioration",
            "google-q2-cogs-ratio",
            {"denominator": "gross revenue"},
        ),
        (
            "discount-refund-q2-deterioration",
            "affiliate-q2-discount-rate",
            {"observation_window": "30_day"},
        ),
    ),
)
def test_evaluators_reject_wrong_metric_estimands(
    scenario_id: str,
    metric_id: str,
    context_update: dict[str, str],
) -> None:
    registration = get_scenario(scenario_id, "1.0")
    generated = registration.generate(_configured_scale())
    actual = list(registration.invariant_suite.metric_observer(generated.dataset))
    index = next(
        index
        for index, comparison in enumerate(actual)
        if comparison.metric_key
        == next(
            metric.metric_key
            for metric in registration.evaluation_spec.ground_truth
            if metric.id == metric_id
        )
    )
    comparison = actual[index]
    assert comparison.definition_context is not None
    actual[index] = comparison.model_copy(
        update={
            "definition_context": comparison.definition_context.model_copy(
                update=context_update
            )
        }
    )

    failures = numeric_ground_truth_failures(
        actual, registration.evaluation_spec.ground_truth
    )
    assert any(metric_id in failure for failure in failures)


@pytest.mark.parametrize(("scenario_id", "observer"), _SCENARIOS)
def test_business_scenario_context_never_reveals_injected_conclusion(
    scenario_id: str,
    observer: Callable,
) -> None:
    registration = get_scenario(scenario_id, "1.0")
    generated = registration.generate(_configured_scale())
    model_text = registration.model_context_contract().model_dump_json().lower()
    document_text = generated.dataset.business_definitions.lower()

    assert "retention deteriorated" not in model_text + document_text
    assert "cogs rose" not in model_text + document_text
    assert "discounts and refunds increased" not in model_text + document_text
    assert all(
        metric.id not in model_text
        for metric in registration.evaluation_spec.ground_truth
    )
