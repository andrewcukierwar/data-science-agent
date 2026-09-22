"""General arithmetic over explicit, complete input tables; no task selection."""

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from statistics import NormalDist
from typing import Any

from schemas.analytical import (
    AnalyticalResult,
    BinaryExperimentRequest,
    CoverageRequest,
    EntityAggregateRequest,
    ExactNumber,
    NumericPolicy,
    Quantity,
    ReconciliationRequest,
    Scope,
)


@dataclass(frozen=True)
class Table:
    columns: tuple[str, ...]
    types: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]

    def require(self, *columns):
        if any(c not in self.columns for c in columns):
            raise ValueError(f"unknown column in {columns}")

    def number(self, row, column, nulls="error") -> Fraction:
        self.require(column)
        value = row[column]
        if value is None:
            if nulls == "zero":
                return Fraction(0)
            raise ValueError(f"null numeric value: {column}")
        data_type = self.types[self.columns.index(column)]
        if re.fullmatch(r"DECIMAL\(\d+,\d+\)", data_type):
            if not isinstance(value, str):
                raise ValueError("DECIMAL must use the retained exact string contract")
            decimal = Decimal(value)
            precision, scale = map(int, re.findall(r"\d+", data_type))
            if not decimal.is_finite():
                raise ValueError("nonfinite DECIMAL")
            number = Fraction(decimal)
            scaled = number * 10**scale
            if scaled.denominator != 1 or abs(scaled) >= 10**precision:
                raise ValueError("DECIMAL does not match declared precision/scale")
            return number
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"not a finite numeric column: {column}")
        return Fraction(value)

    def day(self, row, column, policy):
        self.require(column)
        value = row[column]
        dtype = self.types[self.columns.index(column)]
        if value is None or not isinstance(value, str):
            raise ValueError(f"missing/invalid temporal value: {column}")
        if dtype == "DATE":
            return date.fromisoformat(value)
        if not dtype.startswith("TIMESTAMP") or policy == "date_only":
            raise ValueError(
                "temporal field requires DATE or explicit timestamp date policy"
            )
        stamp = datetime.fromisoformat(value)
        if policy == "utc_date" and stamp.tzinfo is not None:
            return stamp.astimezone(UTC).date()
        if policy == "naive_date" and stamp.tzinfo is None:
            return stamp.date()
        raise ValueError("timestamp timezone does not match date policy")

    def unique(self, key):
        self.require(key)
        seen = set()
        for row in self.rows:
            value = row[key]
            if type(value) not in (str, int) or value in seen:
                raise ValueError(f"duplicate or missing/invalid grain key: {key}")
            seen.add(value)


def exact(value: Fraction) -> ExactNumber:
    return ExactNumber(
        numerator=str(value.numerator), denominator=str(value.denominator)
    )


def number(value, unit, policy: NumericPolicy, reason=None) -> Quantity:
    if value is None:
        return Quantity(
            value=None, unit=unit, representation="undefined", reason=reason
        )
    if isinstance(value, bool):
        return Quantity(value=value, unit=unit, representation="boolean")
    value = Fraction(value)
    if value.denominator == 1:
        return Quantity(
            value=value.numerator, exact=exact(value), unit=unit, representation="exact"
        )
    projected = float(value)
    if not math.isfinite(projected):
        raise ValueError("numeric projection exceeds finite binary64 range")
    error = Fraction(projected) - value
    if error and policy.projection == "exact_only":
        return Quantity(
            value=None,
            exact=exact(value),
            unit=unit,
            representation="undefined",
            reason="exact value is not representable in the float metric schema",
        )
    return Quantity(
        value=projected,
        exact=exact(value),
        unit=unit,
        representation="nearest_binary64" if error else "exact",
        projection_error=exact(error) if error else None,
    )


def approximate(value, unit):
    if not math.isfinite(value):
        raise ValueError("nonfinite statistical result")
    return Quantity(value=value, unit=unit, representation="statistical_binary64")


def scope(request, *, dimensions=None, semantics=None, window=None):
    return Scope(
        population=request.population,
        grain=request.grain,
        period=request.period,
        dimensions=dimensions or {},
        window=window,
        semantics={"date_policy": request.date_policy, **(semantics or {})},
    )


def in_period(day, period):
    return period.start <= day < period.end


def coverage(request: CoverageRequest, table: Table):
    table.require(request.temporal_field)
    if request.dimension_field:
        table.require(request.dimension_field)
    observations = Counter()
    for row in table.rows:
        day = table.day(row, request.temporal_field, request.date_policy)
        if not in_period(day, request.period):
            continue
        dimension = row[request.dimension_field] if request.dimension_field else None
        if request.dimension_field and not isinstance(dimension, str):
            raise ValueError("missing/non-string dimension value")
        observations[(day, dimension)] += 1
    expected = None
    if request.expected_dates is not None:
        expected = sorted(request.expected_dates)
    elif request.cadence == "daily":
        days = (request.period.end - request.period.start).days
        if days > 100_000:
            raise ValueError("expected grid exceeds 100000 cells")
        expected = [request.period.start + timedelta(days=i) for i in range(days)]
    dimensions = request.expected_dimensions or (None,)
    missing = []
    missing_dates = []
    expected_count = None
    if expected is not None:
        expected_count = len(expected) * len(dimensions)
        if expected_count > 100_000:
            raise ValueError("expected grid exceeds 100000 cells")
        missing = [
            (day.isoformat(), dim)
            for day in expected
            for dim in dimensions
            if observations[(day, dim)] == 0
        ]
        missing_dates = [
            day.isoformat()
            for day in expected
            if all(observations[(day, dim)] == 0 for dim in dimensions)
        ]

    def q(v, unit):
        return number(v, unit, request.numeric)

    return [
        AnalyticalResult(
            scope=scope(
                request,
                semantics={
                    "operation": "coverage",
                    "cadence": request.cadence,
                    "expected_dimensions": request.expected_dimensions,
                },
            ),
            method="actual observation counts against caller-supplied expected cells",
            quantities={
                "observation_count": q(sum(observations.values()), "observations"),
                "observed_cell_count": q(len(observations), "cells"),
                "expected_cell_count": q(expected_count, "cells"),
                "missing_cell_count": q(
                    len(missing) if expected is not None else None, "cells"
                ),
                "complete": q(not missing if expected is not None else None, "boolean"),
            },
            warnings=("No expected cadence/date set: completeness not assessed.",)
            if expected is None
            else (),
            details={
                "missing_cells": missing,
                "whole_missing_dates": missing_dates,
                "observed_cells": [
                    {"date": d.isoformat(), "dimension": dim}
                    for d, dim in sorted(observations)
                ],
            },
        )
    ]


def _measure_signature(measure):
    return measure.model_dump(mode="json", exclude={"column"}) if measure else None


def entity_aggregate(
    request: EntityAggregateRequest, entities: Table, events: Table | None
):
    entities.unique(request.entity_key)
    entities.require(request.cohort_date, *request.dimensions.values())
    for measure in (request.numerator, request.denominator):
        if measure is not None and measure.column is not None:
            table = entities if measure.kind == "entity_value" else events
            table.require(measure.column)
    all_entities = {row[request.entity_key]: row for row in entities.rows}
    per_entity = defaultdict(list)
    if events is not None:
        events.unique(request.event_key)
        events.require(request.event_entity_key, request.event_date)
        for row in events.rows:
            day = events.day(row, request.event_date, request.date_policy)
            key = row[request.event_entity_key]
            if key not in all_entities:
                raise ValueError("event references unknown entity")
            per_entity[key].append((day, row))
    groups = defaultdict(list)
    for row in entities.rows:
        acquired = entities.day(row, request.cohort_date, request.date_policy)
        if not in_period(acquired, request.period):
            continue
        dims = tuple(
            sorted((name, row[col]) for name, col in request.dimensions.items())
        )
        if any(not isinstance(value, str) for _, value in dims):
            raise ValueError("missing/non-string entity dimension")
        groups[dims].append((row, acquired))
    if not groups and not request.dimensions:
        groups[()] = []
    results = []
    for dims, members in sorted(groups.items()):
        numerators, denominators = [], []
        incomplete = zero_activity = event_count = null_count = excluded = 0
        for row, acquired in members:
            qualified = []
            if events is not None:
                start = acquired + timedelta(days=request.window.start_day)
                end = acquired + timedelta(days=request.window.end_day)
                if end > request.window.observed_until:
                    incomplete += 1
                    if request.window.maturity == "require_complete":
                        raise ValueError("incomplete observation window")
                    if request.window.maturity == "exclude_incomplete":
                        excluded += 1
                        continue
                qualified = [
                    r
                    for d, r in per_entity[row[request.entity_key]]
                    if start <= d < min(end, request.window.observed_until)
                ]
                if not qualified:
                    zero_activity += 1
                    if not request.include_zero_activity:
                        excluded += 1
                        continue
            event_count += len(qualified)

            def component(measure, qualified=qualified, row=row):
                nonlocal null_count
                if measure.kind == "entity_count":
                    return Fraction(1)
                if measure.kind == "event_count":
                    return Fraction(len(qualified))
                if measure.kind == "event_at_least":
                    return Fraction(int(len(qualified) >= measure.threshold))
                source_rows, table = (
                    ([row], entities)
                    if measure.kind == "entity_value"
                    else (qualified, events)
                )
                table.require(measure.column)
                null_count += sum(r[measure.column] is None for r in source_rows)
                return sum(
                    (
                        table.number(r, measure.column, measure.nulls)
                        for r in source_rows
                    ),
                    Fraction(0),
                )

            numerators.append(component(request.numerator))
            if request.denominator:
                denominators.append(component(request.denominator))
        total_n, total_d = sum(numerators, Fraction(0)), sum(denominators, Fraction(0))
        undefined_count = 0
        value = None
        reason = None
        if request.aggregation == "sum":
            value = total_n
        elif request.aggregation == "mean":
            value = total_n / len(numerators) if numerators else None
            reason = "empty entity population" if not numerators else None
        elif request.aggregation == "ratio_of_sums":
            if total_d:
                value = total_n / total_d
            else:
                undefined_count = 1
        else:
            ratios = []
            for n, d in zip(numerators, denominators, strict=True):
                if not d:
                    undefined_count += 1
                else:
                    ratios.append(n / d)
            if ratios and (
                not undefined_count or request.zero_denominator == "exclude"
            ):
                value = sum(ratios, Fraction(0)) / len(ratios)
        if not numerators and value is None:
            reason = "empty entity population"
        if undefined_count:
            if request.zero_denominator == "error":
                raise ValueError("zero denominator")
            reason = "zero denominator" if value is None else None
        warnings = []
        if incomplete:
            warnings.append(f"Incomplete observation: {request.window.maturity}.")
        if zero_activity and not request.include_zero_activity:
            warnings.append("Zero-activity entities explicitly excluded.")
        if null_count:
            warnings.append("Null numeric inputs explicitly treated as zero.")
        if undefined_count:
            warnings.append(f"Zero denominator: {request.zero_denominator}.")

        def q(v, unit):
            return number(v, unit, request.numeric)

        quantities = {
            "value": number(value, request.unit, request.numeric, reason),
            "numerator_sum": q(total_n, request.numerator.unit),
            "cohort_entities": q(len(members), "entities"),
            "included_entities": q(len(numerators), "entities"),
            "excluded_entities": q(excluded, "entities"),
            "zero_activity_entities": q(zero_activity, "entities"),
            "incomplete_entities": q(incomplete, "entities"),
            "qualifying_events": q(event_count, "events"),
            "null_inputs": q(null_count, "values"),
            "zero_denominators": q(undefined_count, "denominators"),
        }
        if request.denominator:
            quantities["denominator_sum"] = q(total_d, request.denominator.unit)
        results.append(
            AnalyticalResult(
                scope=scope(
                    request,
                    dimensions=dict(dims),
                    window=request.window,
                    semantics={
                        "operation": "entity_aggregate",
                        "aggregation": request.aggregation,
                        "numerator": _measure_signature(request.numerator),
                        "denominator": _measure_signature(request.denominator),
                        "include_zero_activity": request.include_zero_activity,
                        "zero_denominator": request.zero_denominator,
                    },
                ),
                method=f"entity-first {request.aggregation}",
                quantities=quantities,
                warnings=tuple(warnings),
            )
        )
    return results


def reconciliation(request: ReconciliationRequest, table: Table):
    table.unique(request.row_key)
    table.require(request.temporal_field, request.result_column, *request.components)
    residuals, violations = [], []
    for row in table.rows:
        if not in_period(
            table.day(row, request.temporal_field, request.date_policy), request.period
        ):
            continue
        observed = table.number(row, request.result_column)
        expected = Fraction(request.intercept) + sum(
            (
                Fraction(coefficient) * table.number(row, column)
                for column, coefficient in request.components.items()
            ),
            Fraction(0),
        )
        residual = abs(observed - expected)
        tolerance = max(
            Fraction(request.absolute_tolerance),
            Fraction(request.relative_tolerance) * max(abs(observed), abs(expected)),
        )
        residuals.append(residual)
        if residual > tolerance:
            violations.append(row[request.row_key])

    def q(v, unit):
        return number(v, unit, request.numeric)

    return [
        AnalyticalResult(
            scope=scope(
                request,
                semantics={
                    "operation": "linear_reconciliation",
                    "unit": request.unit,
                    "absolute_tolerance": str(request.absolute_tolerance),
                    "relative_tolerance": str(request.relative_tolerance),
                },
            ),
            method=(
                "abs(result - (intercept + weighted components)) "
                "> max(atol, rtol * magnitude)"
            ),
            quantities={
                "checked_rows": q(len(residuals), "rows"),
                "discrepancy_count": q(len(violations), "rows"),
                "max_absolute_residual": q(
                    max(residuals) if residuals else None, request.unit
                ),
            },
            details={"violating_keys": sorted(violations, key=str)},
        )
    ]


def binary_experiment(request: BinaryExperimentRequest, table: Table):
    table.unique(request.subject_id)
    table.require(request.assignment, request.outcome, request.temporal_field)
    arms = {request.control: [], request.treatment: []}
    for row in table.rows:
        if not in_period(
            table.day(row, request.temporal_field, request.date_policy), request.period
        ):
            continue
        assignment, outcome = row[request.assignment], row[request.outcome]
        if assignment not in arms:
            raise ValueError("invalid/missing assignment")
        if type(outcome) not in (int, float, bool) or outcome not in (0, 1):
            raise ValueError("missing/invalid binary outcome")
        arms[assignment].append(int(outcome))
    control, treatment = arms[request.control], arms[request.treatment]
    nc, nt = len(control), len(treatment)
    if not nc or not nt:
        raise ValueError("both arms require observations")
    sc, st = sum(control), sum(treatment)
    pc, pt = Fraction(sc, nc), Fraction(st, nt)
    effect = pt - pc
    pooled = Fraction(sc + st, nc + nt)
    variance_null = pooled * (1 - pooled) * (Fraction(1, nc) + Fraction(1, nt))
    p_value = (
        math.erfc(abs(float(effect)) / math.sqrt(2 * float(variance_null)))
        if variance_null
        else 1.0
    )
    standard_error = math.sqrt(float(pc * (1 - pc) / nc + pt * (1 - pt) / nt))
    z = NormalDist().inv_cdf((1 + request.confidence_level) / 2)
    h = 2 * (math.asin(math.sqrt(float(pt))) - math.asin(math.sqrt(float(pc))))

    def q(v, unit):
        return number(v, unit, request.numeric)

    quantities = {
        "control_denominator": q(nc, "subjects"),
        "treatment_denominator": q(nt, "subjects"),
        "control_successes": q(sc, "successes"),
        "treatment_successes": q(st, "successes"),
        "control_rate": q(pc, "fraction"),
        "treatment_rate": q(pt, "fraction"),
        "estimate": q(effect, "fraction"),
        "confidence_interval_lower": approximate(
            float(effect) - z * standard_error, "fraction"
        ),
        "confidence_interval_upper": approximate(
            float(effect) + z * standard_error, "fraction"
        ),
        "confidence_level": approximate(request.confidence_level, "fraction"),
        "p_value": approximate(p_value, "probability"),
        "effect_size": approximate(h, "cohen_h"),
        "practical_significance_threshold": q(
            Fraction(request.practical_threshold), "fraction"
        ),
        "practically_significant": q(
            abs(effect) >= Fraction(request.practical_threshold), "boolean"
        ),
    }
    warnings = [
        "Calculation alone does not establish randomization, "
        "independence or causal validity."
    ]
    if min(sc, nc - sc, st, nt - st) < 5:
        warnings.append(
            "Sparse/boundary arms: normal approximation and Wald interval "
            "can be unreliable."
        )
    return [
        AnalyticalResult(
            scope=scope(
                request,
                semantics={
                    "operation": "binary_experiment",
                    "outcome": request.outcome,
                    "control": request.control,
                    "treatment": request.treatment,
                    "direction": "treatment-minus-control",
                    "design_assumptions": request.design_assumptions,
                },
            ),
            method=(
                "two-sided pooled two-proportion z test; "
                "unpooled Wald CI; signed Cohen h"
            ),
            quantities=quantities,
            warnings=tuple(warnings),
        )
    ]
