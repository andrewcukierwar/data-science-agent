"""Independent hand-checkable P1.1a fixtures, with no agents/model calls."""

import ast
import copy
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from agents.evidence import has_source_lineage
from agents.result_binding import ResultBindingError, resolve_result
from orchestration.ledger import AnalysisLedger
from schemas.analytical import (
    BinaryExperimentRequest,
    ContrastRequest,
    CoverageRequest,
    EntityAggregateRequest,
    Measure,
    NumericPolicy,
    Period,
    QuantityRef,
    RatioRequest,
    ReconciliationRequest,
    TableInput,
    Window,
)
from schemas.metrics import MetricComparison
from schemas.statistics import StatisticalAssessment
from tools.analytical import (
    AnalyticalExecutionService,
    analytical_binding,
    validate_analytical_record,
)
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager

BASE = date(2032, 2, 10)
NUMERIC = NumericPolicy(projection="nearest_binary64")


def day(offset):
    return BASE + timedelta(days=offset)


def setup(tmp_path, tables, *, order="", max_rows=100_000):
    source = tmp_path / "source"
    source.mkdir(parents=True)
    for name, table in tables.items():
        pq.write_table(pa.table(table), source / f"{name}.parquet")
    workspace = WorkspaceManager(tmp_path / "runs").create_workspace(
        "analytical", inputs_source=source
    )
    ledger = AnalysisLedger(workspace, objective="Independent arithmetic fixture")
    sql = DuckDBExecutionService(workspace, ledger, max_rows=max_rows)
    refs = {}
    for name in tables:
        result = sql.execute(f'SELECT * FROM "{name}" {order}')
        assert result.success
        refs[name] = TableInput(tool_event_id=result.tool_event_id, relation=name)
    return AnalyticalExecutionService(ledger), refs, sql


def base(**updates):
    return dict(
        population="eligible units",
        grain="unit",
        period=Period(start=day(0), end=day(3)),
        numeric=NUMERIC,
        **updates,
    )


def quantity(record, key="value", index=0):
    return record.results[index].quantities[key].value


def rational(record, key="value", index=0):
    number = record.results[index].quantities[key].exact
    return Fraction(int(number.numerator), int(number.denominator))


def cohort_tables():
    return {
        "units": {
            "who": ["a", "b", "c"],
            "joined": [day(0)] * 3,
            "segment": ["Amber", "Amber", "Violet"],
            "exposure": [1, 9, 2],
        },
        "actions": {
            "token": [1, 2, 3, 4, 5],
            "actor": ["a", "a", "a", "b", "b"],
            "happened": [day(0), day(1), day(2), day(-1), day(3)],
            "paid": [
                Decimal("10.10"),
                Decimal("20.20"),
                Decimal("999.00"),
                Decimal("500.00"),
                Decimal("400.00"),
            ],
        },
    }


def cohort_request(refs, **changes):
    options = dict(
        **base(),
        entities=refs["units"],
        entity_key="who",
        cohort_date="joined",
        events=refs["actions"],
        event_key="token",
        event_entity_key="actor",
        event_date="happened",
        window=Window(
            start_day=0, end_day=2, observed_until=day(5), maturity="require_complete"
        ),
        include_zero_activity=True,
        numerator=Measure(
            kind="event_sum",
            semantic_key="post-entry value",
            unit="credit",
            column="paid",
        ),
        denominator=Measure(
            kind="entity_count", semantic_key="eligible units", unit="entities"
        ),
        aggregation="ratio_of_sums",
        unit="credit/entity",
    )
    options.update(changes)
    return EntityAggregateRequest(**options)


def test_entity_first_zero_activity_decimal_and_exact_boundaries(tmp_path):
    service, refs, _ = setup(tmp_path, cohort_tables())
    record = service.execute(cohort_request(refs))
    # Exactly two in-window events for a, none for b or c. Bounds [0,2).
    assert rational(record, "numerator_sum") == Fraction(303, 10)
    assert quantity(record, "denominator_sum") == 3
    assert rational(record) == Fraction(101, 10)
    assert quantity(record, "included_entities") == 3
    assert quantity(record, "zero_activity_entities") == 2
    assert quantity(record, "qualifying_events") == 2
    assert record.results[0].quantities["value"].projection_error is not None
    acquired = service.execute(
        cohort_request(
            refs,
            numerator=Measure(
                kind="entity_count", semantic_key="entered", unit="entities"
            ),
            denominator=None,
            aggregation="sum",
            unit="entities",
        )
    )
    repeated = service.execute(
        cohort_request(
            refs,
            numerator=Measure(
                kind="event_at_least",
                semantic_key="two events",
                unit="entities",
                threshold=2,
            ),
            denominator=None,
            aggregation="sum",
            unit="entities",
        )
    )
    assert quantity(acquired) == 3  # Not 5 joined action rows, nor 2 active rows.
    assert quantity(repeated) == 1


@pytest.mark.parametrize(
    "policy,expected", [("include_partial", 3), ("exclude_incomplete", 0)]
)
def test_maturity_outcomes_are_explicit(tmp_path, policy, expected):
    service, refs, _ = setup(tmp_path, cohort_tables())
    request = cohort_request(
        refs,
        window=Window(start_day=0, end_day=2, observed_until=day(1), maturity=policy),
    )
    result = service.execute(request)
    assert quantity(result, "incomplete_entities") == 3
    assert quantity(result, "included_entities") == expected
    assert result.results[0].warnings
    with pytest.raises(ValueError, match="incomplete observation"):
        service.execute(
            request.model_copy(
                update={
                    "window": request.window.model_copy(
                        update={"maturity": "require_complete"}
                    )
                }
            )
        )


@pytest.mark.parametrize("source,key", [("units", "who"), ("actions", "token")])
def test_duplicate_grain_from_dimension_join_is_rejected(tmp_path, source, key):
    tables = cohort_tables()
    for values in tables[source].values():
        values.append(values[0])
    service, refs, _ = setup(tmp_path, tables)
    with pytest.raises(ValueError, match="duplicate.*grain"):
        service.execute(cohort_request(refs))
    assert service.ledger.tool_events[-1].status.value == "failed"
    assert service.ledger.tool_events[-1].output is None
    assert service.ledger.budget.python_executions == 1


def aggregate_request(ref, **changes):
    options = dict(
        **base(),
        entities=ref,
        entity_key="id",
        cohort_date="when",
        include_zero_activity=True,
        numerator=Measure(
            kind="entity_value", semantic_key="yield", unit="credit", column="n"
        ),
        denominator=Measure(
            kind="entity_value", semantic_key="exposure", unit="units", column="d"
        ),
        aggregation="ratio_of_sums",
        unit="credit/unit",
    )
    options.update(changes)
    return EntityAggregateRequest(**options)


def unequal_table():
    return {
        "raw": {
            "id": ["x", "y"],
            "when": [day(0), day(1)],
            "n": [1, 9],
            "d": [1, 3],
            "bucket": ["Amber", "Violet"],
        }
    }


def test_ratio_of_sums_and_mean_of_ratios_are_distinct(tmp_path):
    service, refs, _ = setup(tmp_path, unequal_table())
    request = aggregate_request(refs["raw"])
    sums = service.execute(request)
    means = service.execute(
        request.model_copy(update={"aggregation": "mean_of_ratios"})
    )
    assert quantity(sums) == 2.5  # (1+9)/(1+3)
    assert quantity(means) == 2  # (1/1 + 9/3)/2
    with pytest.raises(ValueError, match="semantics"):
        service.execute(
            ContrastRequest(
                baseline=QuantityRef(tool_event_id=sums.tool_event_id),
                comparison=QuantityRef(tool_event_id=means.tool_event_id),
                comparison_type="absolute_difference",
                numeric=NUMERIC,
            )
        )


@pytest.mark.parametrize("operation", ["ratio_of_sums", "mean_of_ratios"])
def test_zero_denominator_and_null_handling(tmp_path, operation):
    tables = unequal_table()
    tables["raw"]["d"] = [0, 0]
    service, refs, _ = setup(tmp_path, tables)
    request = aggregate_request(refs["raw"], aggregation=operation)
    result = service.execute(request)
    assert quantity(result) is None
    assert "zero denominator" in result.results[0].quantities["value"].reason
    with pytest.raises(ValueError, match="zero denominator"):
        service.execute(request.model_copy(update={"zero_denominator": "error"}))


def test_explicit_exclusion_null_zero_and_empty_population(tmp_path):
    tables = unequal_table()
    tables["raw"]["n"] = [None, 9]
    tables["raw"]["d"] = [0, 3]
    service, refs, _ = setup(tmp_path, tables)
    request = aggregate_request(
        refs["raw"], aggregation="mean_of_ratios", zero_denominator="exclude"
    )
    with pytest.raises(ValueError, match="null numeric"):
        service.execute(request)
    result = service.execute(
        request.model_copy(
            update={"numerator": request.numerator.model_copy(update={"nulls": "zero"})}
        )
    )
    assert quantity(result) == 3
    assert quantity(result, "null_inputs") == 1
    assert quantity(result, "zero_denominators") == 1
    assert result.results[0].warnings
    empty = service.execute(
        request.model_copy(update={"period": Period(start=day(20), end=day(23))})
    )
    assert quantity(empty) is None


def test_independent_totals_ratio_and_contrasts_check_scope(tmp_path):
    service, refs, _ = setup(tmp_path, unequal_table())
    request = aggregate_request(
        refs["raw"], denominator=None, aggregation="sum", unit="credit"
    )
    total = service.execute(request)
    count = service.execute(
        request.model_copy(
            update={
                "numerator": Measure(
                    kind="entity_count", semantic_key="units", unit="entities"
                ),
                "unit": "entities",
            }
        )
    )
    ratio = service.execute(
        RatioRequest(
            numerator=QuantityRef(tool_event_id=total.tool_event_id),
            denominator=QuantityRef(tool_event_id=count.tool_event_id),
            unit="credit/entity",
            semantic_key="value per unit",
            numeric=NUMERIC,
        )
    )
    assert quantity(ratio) == 5
    segments = service.execute(
        request.model_copy(update={"dimensions": {"category": "bucket"}})
    )
    assert quantity(segments, index=0) == 1
    assert quantity(segments, index=1) == 9
    for other in (
        segments,
        service.execute(request.model_copy(update={"population": "different units"})),
        service.execute(
            request.model_copy(update={"period": Period(start=day(0), end=day(4))})
        ),
    ):
        with pytest.raises(ValueError, match="incompatible"):
            service.execute(
                ContrastRequest(
                    baseline=QuantityRef(tool_event_id=total.tool_event_id),
                    comparison=QuantityRef(tool_event_id=other.tool_event_id),
                    comparison_type="absolute_difference",
                    numeric=NUMERIC,
                )
            )
    level = service.execute(
        ContrastRequest(
            baseline=QuantityRef(tool_event_id=total.tool_event_id),
            comparison_type="level",
            numeric=NUMERIC,
        )
    )
    assert quantity(level) == 10


def test_period_difference_relative_change_negative_and_zero_baseline(tmp_path):
    table = {
        "raw": {"id": [1, 2, 3], "when": [day(0), day(3), day(6)], "n": [-10, -5, 0]}
    }
    service, refs, _ = setup(tmp_path, table)
    request = aggregate_request(
        refs["raw"], denominator=None, aggregation="sum", unit="credit"
    )
    records = [
        service.execute(
            request.model_copy(update={"period": Period(start=day(i), end=day(i + 3))})
        )
        for i in (0, 3, 6)
    ]

    def contrast(a, b, kind):
        return service.execute(
            ContrastRequest(
                baseline=QuantityRef(tool_event_id=a.tool_event_id),
                comparison=QuantityRef(tool_event_id=b.tool_event_id),
                comparison_type=kind,
                numeric=NUMERIC,
            )
        )

    assert quantity(contrast(records[0], records[1], "absolute_difference")) == 5
    negative = contrast(records[0], records[1], "relative_change")
    assert quantity(negative) == -0.5
    assert negative.results[0].warnings
    assert quantity(contrast(records[2], records[1], "relative_change")) is None


@pytest.mark.parametrize(
    "missing,whole,cells",
    [
        ([], [], []),
        (
            [2, 3],
            [day(1).isoformat()],
            [(day(1).isoformat(), "A"), (day(1).isoformat(), "B")],
        ),
        ([5], [], [(day(2).isoformat(), "B")]),
    ],
)
def test_expected_grid_counts_actual_observations(tmp_path, missing, whole, cells):
    rows = [(day(d), label) for d in range(3) for label in ("A", "B")]
    rows = [row for i, row in enumerate(rows) if i not in missing]
    service, refs, _ = setup(
        tmp_path,
        {"dispatch": {"marked": [r[0] for r in rows], "zone": [r[1] for r in rows]}},
    )
    record = service.execute(
        CoverageRequest(
            **base(),
            source=refs["dispatch"],
            temporal_field="marked",
            cadence="daily",
            dimension_field="zone",
            expected_dimensions=("A", "B"),
        )
    )
    assert quantity(record, "complete") == (not missing)
    assert quantity(record, "observation_count") == len(rows)
    assert record.results[0].details["whole_missing_dates"] == whole
    assert record.results[0].details["missing_cells"] == cells


def test_sparse_events_without_expectation_and_explicit_date_set(tmp_path):
    service, refs, _ = setup(tmp_path, {"pulses": {"at": [day(0), day(2)]}})
    request = CoverageRequest(**base(), source=refs["pulses"], temporal_field="at")
    sparse = service.execute(request)
    assert quantity(sparse, "complete") is None
    assert quantity(sparse, "missing_cell_count") is None
    explicit = service.execute(
        request.model_copy(update={"expected_dates": (day(0), day(2))})
    )
    assert quantity(explicit, "complete") is True


def test_reconciliation_separates_float_noise_from_material_error(tmp_path):
    service, refs, _ = setup(
        tmp_path,
        {
            "balance": {
                "id": [1, 2],
                "when": [day(0)] * 2,
                "result": [100.0000000000001, 100.02],
                "gross": [150.0, 150.0],
                "deduction": [50.0, 50.0],
            }
        },
    )
    record = service.execute(
        ReconciliationRequest(
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
    )
    assert quantity(record, "discrepancy_count") == 1
    assert record.results[0].details["violating_keys"] == [2]
    assert quantity(record, "max_absolute_residual") == pytest.approx(0.02)


def experiment(tmp_path, nc, sc, nt, st, **changes):
    service, refs, _ = setup(
        tmp_path,
        {
            "trial": {
                "unit": [f"u{i}" for i in range(nc + nt)],
                "stamp": [day(0)] * (nc + nt),
                "arm": ["reference"] * nc + ["candidate"] * nt,
                "response": [1] * sc + [0] * (nc - sc) + [1] * st + [0] * (nt - st),
            }
        },
    )
    options = dict(
        **base(),
        source=refs["trial"],
        subject_id="unit",
        assignment="arm",
        temporal_field="stamp",
        control="reference",
        treatment="candidate",
        outcome="response",
        confidence_level=0.95,
        practical_threshold=Decimal(".05"),
        design_assumptions=("Independent assigned subjects; design unverified",),
    )
    options.update(changes)
    request = BinaryExperimentRequest(**options)
    return service, request, service.execute(request)


# Independently tabulated normal-tail/Wald values; rational count calculations
# are stated here, not derived by calling the production primitive as oracle.
# Tabulation cross-checked with statsmodels proportions_ztest,
# confint_proportions_2indep(method="wald"), and proportion_effectsize.
@pytest.mark.parametrize(
    "counts,effect,p,lower,upper,h,practical",
    [
        (
            (100, 40, 100, 60),
            0.2,
            0.004677734981047266,
            0.06420971191085941,
            0.3357902880891406,
            0.40271584158066176,
            True,
        ),
        (
            (100, 40, 100, 40),
            0.0,
            1.0,
            -0.1357902880891406,
            0.1357902880891406,
            0.0,
            False,
        ),
        (
            (10000, 5000, 10000, 5200),
            0.02,
            0.0046694723203575105,
            0.006146506480967404,
            0.033853493519032635,
            0.04001067435398986,
            False,
        ),
    ],
)
def test_binary_procedure_independent_expected_values(
    tmp_path, counts, effect, p, lower, upper, h, practical
):
    _, _, record = experiment(tmp_path, *counts)
    assert quantity(record, "estimate") == pytest.approx(effect, abs=1e-15)
    assert quantity(record, "p_value") == pytest.approx(p, rel=1e-7)
    assert quantity(record, "confidence_interval_lower") == pytest.approx(
        lower, abs=1e-8
    )
    assert quantity(record, "confidence_interval_upper") == pytest.approx(
        upper, abs=1e-8
    )
    assert quantity(record, "effect_size") == pytest.approx(h, abs=1e-10)
    assert quantity(record, "practically_significant") is practical
    assert "causal" in record.results[0].warnings[0]


def test_arm_swap_unequal_sizes_and_p02_exact_propagation(tmp_path):
    service, request, result = experiment(tmp_path, 80, 16, 120, 60)
    swapped = service.execute(
        request.model_copy(
            update={"control": request.treatment, "treatment": request.control}
        )
    )
    assert rational(result, "estimate") == Fraction(3, 10)
    assert rational(swapped, "estimate") == Fraction(-3, 10)
    assert quantity(result, "p_value") == quantity(swapped, "p_value")
    assert quantity(result, "effect_size") == -quantity(swapped, "effect_size")
    fields = {
        field: field.replace(".", "_")
        for field in StatisticalAssessment.numerical_fields
    }
    claim = StatisticalAssessment(
        metric_key="response contrast",
        baseline_period="reference",
        comparison_period="candidate",
        method=result.results[0].method,
        unit_of_analysis="subject",
        conclusion="significant_and_practical",
        assumptions_checked=("Independent assigned subjects; design unverified",),
        causal_interpretation="association_only",
        evidence_refs=[result.tool_event_id],
        computation=analytical_binding(result, fields),
    )
    resolved = resolve_result(claim, service.ledger)
    assert resolved.p_value == quantity(result, "p_value")
    assert resolved.estimate == 0.3
    with pytest.raises(ResultBindingError, match="contradicts"):
        resolve_result(claim.model_copy(update={"p_value": 1.0}), service.ledger)
    reloaded = AnalysisLedger(service.ledger.state_path)
    assert resolve_result(resolved, reloaded) == resolved


@pytest.mark.parametrize(
    "field,values,error",
    [
        ("id", ["a", "a"], "duplicate"),
        ("arm", ["left", "unknown"], "assignment"),
        ("outcome", [1, None], "outcome"),
        ("outcome", [1, 2], "outcome"),
    ],
)
def test_invalid_binary_data_fails_clearly(tmp_path, field, values, error):
    data = {
        "id": ["a", "b"],
        "date": [day(0)] * 2,
        "arm": ["left", "right"],
        "outcome": [1, 0],
    }
    data[field] = values
    service, refs, _ = setup(tmp_path, {"test": data})
    request = BinaryExperimentRequest(
        **base(),
        source=refs["test"],
        subject_id="id",
        assignment="arm",
        temporal_field="date",
        control="left",
        treatment="right",
        outcome="outcome",
        confidence_level=0.9,
        practical_threshold=Decimal(".1"),
        design_assumptions=("Unverified",),
    )
    with pytest.raises(ValueError, match=error):
        service.execute(request)


def test_exact_decimal_projection_requires_explicit_policy(tmp_path):
    service, refs, _ = setup(
        tmp_path,
        {
            "raw": {
                "id": [1, 2],
                "when": [day(0)] * 2,
                "n": [Decimal("0.10"), Decimal("0.20")],
            }
        },
    )
    request = aggregate_request(
        refs["raw"],
        denominator=None,
        aggregation="sum",
        unit="credit",
        numeric=NumericPolicy(projection="exact_only"),
    )
    exact_result = service.execute(request)
    assert rational(exact_result) == Fraction(3, 10)
    assert quantity(exact_result) is None  # No silent float cast.
    rounded = service.execute(request.model_copy(update={"numeric": NUMERIC}))
    assert rational(rounded) == Fraction(3, 10)
    assert quantity(rounded) == 0.3
    error = rounded.results[0].quantities["value"].projection_error
    assert Fraction(int(error.numerator), int(error.denominator)) == Fraction(
        0.3
    ) - Fraction(3, 10)
    claim = MetricComparison(
        metric_key="total",
        baseline_period="period",
        comparison_period="period",
        comparison_type="level",
        unit="credit",
        evidence_refs=[rounded.tool_event_id],
        computation=analytical_binding(rounded, {"value": "value"}),
    )
    assert resolve_result(claim, service.ledger).value == 0.3
    with pytest.raises(ResultBindingError, match="finite typed"):
        resolve_result(
            claim.model_copy(
                update={
                    "computation": analytical_binding(exact_result, {"value": "value"})
                }
            ),
            service.ledger,
        )


def test_truncated_failed_and_numeric_string_inputs_are_rejected(tmp_path):
    service, refs, sql = setup(
        tmp_path, {"raw": {"id": [1, 2], "when": [day(0)] * 2, "n": ["1.00", "2.00"]}}
    )
    request = aggregate_request(
        refs["raw"], denominator=None, aggregation="sum", unit="credit"
    )
    with pytest.raises(ValueError, match="numeric"):
        service.execute(request)
    failed = sql.execute("SELECT missing_column")
    with pytest.raises(ValueError, match="successful"):
        service.execute(
            request.model_copy(
                update={
                    "entities": TableInput(
                        tool_event_id=failed.tool_event_id, relation="raw"
                    )
                }
            )
        )
    sql.max_rows = 1
    truncated = sql.execute("SELECT * FROM raw")
    with pytest.raises(ValueError, match="untruncated"):
        service.execute(
            request.model_copy(
                update={
                    "entities": TableInput(
                        tool_event_id=truncated.tool_event_id, relation="raw"
                    )
                }
            )
        )


def test_tampered_outputs_and_dependencies_cannot_be_bound(tmp_path):
    service, refs, _ = setup(tmp_path, unequal_table())
    record = service.execute(aggregate_request(refs["raw"]))
    event = service.ledger.tool_events[-1]
    original = copy.deepcopy(event.output)
    event.output["record"]["results"][0]["quantities"]["value"]["value"] = 123
    with pytest.raises(ValueError, match="identity"):
        validate_analytical_record(service.ledger, event.id)
    event.output = original
    service.ledger.tool_events[0].output["rows"][0][2] = 999
    with pytest.raises(ValueError, match="input changed"):
        validate_analytical_record(service.ledger, event.id)
    assert not has_source_lineage(service.ledger, [record.tool_event_id])


def test_generality_renaming_translation_and_permutation(tmp_path):
    tables = cohort_tables()
    service, refs, _ = setup(tmp_path / "original", tables)
    original = service.execute(cohort_request(refs, dimensions={"category": "segment"}))
    renamed = {"people": {}, "touches": {}}
    entity_names = {
        "who": "subject",
        "joined": "began",
        "segment": "kind",
        "exposure": "size",
    }
    event_names = {
        "token": "eid",
        "actor": "pid",
        "happened": "stamp",
        "paid": "amount",
    }
    for old, new, mapping in (
        ("units", "people", entity_names),
        ("actions", "touches", event_names),
    ):
        for name, values in tables[old].items():
            renamed[new][mapping[name]] = [
                v + timedelta(days=100)
                if isinstance(v, date)
                else {"Amber": "One", "Violet": "Two"}.get(v, v)
                for v in reversed(values)
            ]
    other, inputs, _ = setup(tmp_path / "renamed", renamed)
    request = cohort_request(
        refs,
        entities=inputs["people"],
        entity_key="subject",
        cohort_date="began",
        events=inputs["touches"],
        event_key="eid",
        event_entity_key="pid",
        event_date="stamp",
        dimensions={"renamed_category": "kind"},
        period=Period(start=day(100), end=day(103)),
        window=Window(
            start_day=0, end_day=2, observed_until=day(105), maturity="require_complete"
        ),
        numerator=Measure(
            kind="event_sum",
            semantic_key="post-entry value",
            unit="credit",
            column="amount",
        ),
    )
    transformed = other.execute(request)
    assert [r.quantities for r in original.results] == [
        r.quantities for r in transformed.results
    ]
    assert [r.scope.dimensions for r in transformed.results] == [
        {"renamed_category": "One"},
        {"renamed_category": "Two"},
    ]


def test_production_analytical_modules_have_no_hidden_dependencies():
    paths = [
        *Path("src/analytics").glob("*.py"),
        Path("src/tools/analytical.py"),
        Path("src/schemas/analytical.py"),
    ]
    forbidden = {"scenarios", "evaluation", "tests", "benchmark"}
    for path in paths:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    alias.name.split(".")[0] not in forbidden for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden
        assert not any(
            token in path.read_text()
            for token in ("Meta", "Google", "Affiliate", '"Q1"', '"Q2"')
        )


def test_retained_record_is_inspectable_without_recomputation(tmp_path):
    from agents import inspect_evidence
    from tests.test_agent_runtime import _invoke
    from tests.test_result_binding import _context

    context = _context(tmp_path)
    source = context.sql_service.execute(
        "SELECT 1 AS id, DATE '2032-02-10' AS when, revenue AS n FROM orders"
    )
    assert source.success
    service = AnalyticalExecutionService(context.ledger)
    record = service.execute(
        aggregate_request(
            TableInput(tool_event_id=source.tool_event_id, relation="orders"),
            denominator=None,
            aggregation="sum",
            unit="credit",
        )
    )
    context.ledger = AnalysisLedger(context.ledger.state_path)
    context.run_config = context.run_config.model_copy(
        update={"max_text_chars": 100_000}
    )
    before = context.ledger.budget.model_dump()
    inspected = _invoke(inspect_evidence, context, {"reference": record.tool_event_id})
    assert inspected.success and inspected.data["result_available"]
    assert inspected.data["provenance_verified"]
    assert inspected.data["output"]["record"] == record.model_dump(mode="json")
    assert context.ledger.budget.model_dump() == before
    assert len(context.ledger.tool_events) == 2


def test_large_fixed_point_integer_is_not_silently_rounded_for_binding(tmp_path):
    service, refs, _ = setup(
        tmp_path,
        {
            "raw": {
                "id": [1],
                "when": [day(0)],
                "n": [Decimal("9007199254740993")],
            }
        },
    )
    record = service.execute(
        aggregate_request(
            refs["raw"], denominator=None, aggregation="sum", unit="credit"
        )
    )
    assert quantity(record) == 9007199254740993
    assert rational(record) == Fraction(9007199254740993)
    claim = MetricComparison(
        metric_key="total",
        baseline_period="period",
        comparison_period="period",
        comparison_type="level",
        unit="credit",
        evidence_refs=[record.tool_event_id],
        computation=analytical_binding(record, {"value": "value"}),
    )
    with pytest.raises(ResultBindingError, match="represented exactly"):
        resolve_result(claim, service.ledger)


def test_component_and_indicator_ratio_building_blocks(tmp_path):
    service, refs, _ = setup(
        tmp_path,
        {
            "raw": {
                "id": [1, 2],
                "when": [day(0), day(1)],
                "n": [2, 9],
                "d": [10, 30],
                "responded": [0, 1],
            }
        },
    )
    request = aggregate_request(refs["raw"])
    components = service.execute(request)
    assert rational(components) == Fraction(11, 40)
    response = service.execute(
        request.model_copy(
            update={
                "numerator": Measure(
                    kind="entity_value",
                    column="responded",
                    semantic_key="responded",
                    unit="responses",
                ),
                "denominator": Measure(
                    kind="entity_count", semantic_key="eligible units", unit="entities"
                ),
                "unit": "fraction",
            }
        )
    )
    assert rational(response) == Fraction(1, 2)
