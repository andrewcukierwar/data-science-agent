"""Checkpoint completion: required scope compatibility cannot be label-only."""

from decimal import Decimal

import pytest

from schemas.analytical import (
    ContrastRequest,
    CoverageRequest,
    Measure,
    Period,
    QuantityRef,
    RatioRequest,
    ReconciliationRequest,
)
from tests.test_analytical_primitives import (
    NUMERIC,
    aggregate_request,
    base,
    cohort_request,
    cohort_tables,
    day,
    experiment,
    setup,
)


def contrast(service, first, second, quantity="value"):
    return service.execute(
        ContrastRequest(
            baseline=QuantityRef(tool_event_id=first.tool_event_id, quantity=quantity),
            comparison=QuantityRef(
                tool_event_id=second.tool_event_id, quantity=quantity
            ),
            comparison_type="absolute_difference",
            numeric=NUMERIC,
        )
    )


def test_ratio_rejects_different_inclusion_populations(tmp_path):
    service, refs, _ = setup(tmp_path, cohort_tables())
    numerator = service.execute(
        cohort_request(
            refs,
            denominator=None,
            aggregation="sum",
            unit="credit",
            include_zero_activity=False,
        )
    )
    denominator = service.execute(
        cohort_request(
            refs,
            numerator=Measure(
                kind="entity_count", semantic_key="eligible units", unit="entities"
            ),
            denominator=None,
            aggregation="sum",
            unit="entities",
            include_zero_activity=True,
        )
    )
    with pytest.raises(ValueError, match="incompatible"):
        service.execute(
            RatioRequest(
                numerator=QuantityRef(tool_event_id=numerator.tool_event_id),
                denominator=QuantityRef(tool_event_id=denominator.tool_event_id),
                unit="credit/entity",
                semantic_key="value per unit",
                numeric=NUMERIC,
            )
        )


def test_derived_contrasts_retain_baseline_windows(tmp_path):
    service, refs, _ = setup(
        tmp_path,
        {
            "raw": {
                "id": [1, 2, 3],
                "when": [day(0), day(3), day(6)],
                "n": [1, 4, 9],
            }
        },
    )
    request = aggregate_request(
        refs["raw"], denominator=None, aggregation="sum", unit="credit"
    )
    periods = [
        service.execute(
            request.model_copy(update={"period": Period(start=day(i), end=day(i + 3))})
        )
        for i in (0, 3, 6)
    ]
    first = contrast(service, periods[0], periods[2])
    second = contrast(service, periods[1], periods[2])
    with pytest.raises(ValueError, match="incompatible"):
        contrast(service, first, second)
    # A difference is not an aggregate total, even if its inputs were sums.
    with pytest.raises(ValueError, match="aggregate totals"):
        service.execute(
            RatioRequest(
                numerator=QuantityRef(tool_event_id=first.tool_event_id),
                denominator=QuantityRef(tool_event_id=second.tool_event_id),
                unit="fraction",
                semantic_key="ratio",
                numeric=NUMERIC,
            )
        )


def test_coverage_contrast_requires_same_expectation(tmp_path):
    service, refs, _ = setup(tmp_path, {"pulses": {"at": [day(0)]}})
    request = CoverageRequest(
        **base(), source=refs["pulses"], temporal_field="at", expected_dates=(day(0),)
    )
    first = service.execute(request)
    second = service.execute(
        request.model_copy(update={"expected_dates": (day(0), day(2))})
    )
    with pytest.raises(ValueError, match="incompatible"):
        contrast(service, first, second, "missing_cell_count")


def test_reconciliation_contrast_requires_same_identity(tmp_path):
    service, refs, _ = setup(
        tmp_path,
        {
            "balance": {
                "id": [1],
                "when": [day(0)],
                "result": [100],
                "gross": [150],
                "deduction": [50],
            }
        },
    )
    request = ReconciliationRequest(
        **base(),
        source=refs["balance"],
        row_key="id",
        temporal_field="when",
        result_column="result",
        components={"gross": Decimal(1), "deduction": Decimal(-1)},
        unit="credit",
        absolute_tolerance=Decimal(".005"),
        relative_tolerance=Decimal(0),
    )
    first = service.execute(request)
    second = service.execute(
        request.model_copy(update={"components": {"gross": Decimal(1)}})
    )
    with pytest.raises(ValueError, match="incompatible"):
        contrast(service, first, second, "max_absolute_residual")


def test_interval_contrast_requires_same_confidence_level(tmp_path):
    service, request, first = experiment(tmp_path, 100, 40, 100, 60)
    second = service.execute(request.model_copy(update={"confidence_level": 0.9}))
    with pytest.raises(ValueError, match="incompatible"):
        contrast(service, first, second, "confidence_interval_lower")


@pytest.mark.parametrize(
    "choice,value", [("start_day", 1), ("end_day", 3), ("maturity", "include_partial")]
)
def test_contrast_rejects_changed_observation_window(tmp_path, choice, value):
    service, refs, _ = setup(tmp_path, cohort_tables())
    request = cohort_request(refs)
    first = service.execute(request)
    second = service.execute(
        request.model_copy(
            update={"window": request.window.model_copy(update={choice: value})}
        )
    )
    with pytest.raises(ValueError, match="incompatible observation"):
        contrast(service, first, second)


def test_ratio_keeps_selected_component_roles(tmp_path):
    from tests.test_analytical_primitives import unequal_table

    service, refs, _ = setup(tmp_path, unequal_table())
    components = service.execute(aggregate_request(refs["raw"]))
    request = RatioRequest(
        numerator=QuantityRef(
            tool_event_id=components.tool_event_id, quantity="numerator_sum"
        ),
        denominator=QuantityRef(
            tool_event_id=components.tool_event_id, quantity="denominator_sum"
        ),
        unit="fraction",
        semantic_key="component ratio",
        numeric=NUMERIC,
    )
    first = service.execute(request)
    second = service.execute(
        request.model_copy(
            update={"numerator": request.denominator, "denominator": request.numerator}
        )
    )
    with pytest.raises(ValueError, match="incompatible"):
        contrast(service, first, second)
