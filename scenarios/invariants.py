"""Common deterministic invariants for generated scenario source bundles."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from math import isfinite

import numpy as np
import pandas as pd

from scenarios.definitions.models import GroundTruthMetric
from schemas.metrics import (
    MetricComparison,
    metric_definition_contexts_match,
    normalize_metric_comparison,
    normalize_metric_key,
    normalize_metric_period,
    normalize_metric_unit,
    normalized_dimension_mapping,
)

TableMap = Mapping[str, pd.DataFrame]
RowPredicate = Callable[[pd.DataFrame], pd.Series | bool]
MetricObserver = Callable[[object], Sequence[MetricComparison]]


@dataclass(frozen=True, slots=True)
class InvariantViolation:
    """One stable, actionable source or ground-truth invariant failure."""

    invariant_id: str
    message: str


@dataclass(frozen=True, slots=True)
class InvariantReport:
    """Deterministic result of a generated-source invariant run."""

    violations: tuple[InvariantViolation, ...] = ()

    @property
    def passed(self) -> bool:
        """Whether all declared invariants passed."""

        return not self.violations

    def assert_valid(self) -> None:
        """Raise one stable error when the generated source is invalid."""

        if self.violations:
            details = "; ".join(
                f"{item.invariant_id}: {item.message}" for item in self.violations
            )
            raise ScenarioInvariantError(details)


class ScenarioInvariantError(ValueError):
    """Raised when a generated dataset violates its declared invariants."""


@dataclass(frozen=True, slots=True)
class KeyInvariant:
    table: str
    columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DateInvariant:
    table: str
    column: str
    minimum: date | None = None
    maximum: date | None = None


@dataclass(frozen=True, slots=True)
class ForeignKeyInvariant:
    child_table: str
    child_columns: tuple[str, ...]
    parent_table: str
    parent_columns: tuple[str, ...]
    nullable: bool = False


@dataclass(frozen=True, slots=True)
class DateRelationInvariant:
    child_table: str
    child_date_column: str
    parent_table: str
    parent_key: str
    parent_date_column: str
    child_key: str
    relation: str = "ge"


@dataclass(frozen=True, slots=True)
class DocumentedNullInvariant:
    table: str
    column: str
    allowed_when: RowPredicate
    documentation_terms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EconomicIdentityInvariant:
    invariant_id: str
    table: str
    result_column: str
    operand_columns: tuple[str, ...]
    expected: Callable[[pd.DataFrame], pd.Series]
    documentation_terms: tuple[str, ...] = ()
    tolerance: float = 1e-3


@dataclass(frozen=True, slots=True)
class RowInvariant:
    invariant_id: str
    table: str
    predicate: RowPredicate
    description: str


@dataclass(frozen=True, slots=True)
class DatasetInvariantSpec:
    """Declarative checks shared by clean and transformed source bundles."""

    keys: tuple[KeyInvariant, ...] = ()
    dates: tuple[DateInvariant, ...] = ()
    foreign_keys: tuple[ForeignKeyInvariant, ...] = ()
    date_relations: tuple[DateRelationInvariant, ...] = ()
    documented_nulls: tuple[DocumentedNullInvariant, ...] = ()
    economic_identities: tuple[EconomicIdentityInvariant, ...] = ()
    row_invariants: tuple[RowInvariant, ...] = ()


def _tables(source: object | TableMap) -> TableMap:
    if isinstance(source, Mapping):
        return source
    table_map = getattr(source, "table_map", None)
    if table_map is None or not callable(table_map):
        raise TypeError("source must be a table mapping or expose table_map()")
    return table_map()


def _documentation(source: object | TableMap) -> str:
    return str(getattr(source, "business_definitions", ""))


def _violation(invariant_id: str, message: str) -> InvariantViolation:
    return InvariantViolation(invariant_id=invariant_id, message=message)


# A single generated document is shared by the clean baseline and by every
# scenario injected on top of it, so any sentence asserting injection status is
# true for at most one of them. Such a sentence is model-visible, so a scenario
# that inherits it hands the agent a false premise about its own source. The
# document may describe metrics and data treatment; it must not claim whether a
# scenario was injected.
BASELINE_ONLY_DOCUMENT_CLAIMS: tuple[str, ...] = (
    "clean baseline",
    "no business or data-quality scenario is injected",
    "no scenario is injected",
    "no injection",
    "no injected",
    "not injected",
    "free of injected",
    "without injected",
)


def baseline_only_document_claims(source: object | TableMap) -> tuple[str, ...]:
    """Return every baseline-only injection claim found in the source document."""

    documentation = _documentation(source).lower()
    return tuple(
        claim for claim in BASELINE_ONLY_DOCUMENT_CLAIMS if claim in documentation
    )


def _columns_present(
    tables: TableMap,
    table: str,
    columns: Sequence[str],
    invariant_id: str,
) -> InvariantViolation | None:
    frame = tables.get(table)
    if frame is None:
        return _violation(invariant_id, f"table {table!r} is missing")
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        return _violation(
            invariant_id,
            f"table {table!r} is missing columns: {', '.join(missing)}",
        )
    return None


def check_dataset_invariants(
    source: object | TableMap,
    spec: DatasetInvariantSpec,
) -> tuple[InvariantViolation, ...]:
    """Check keys, dates, references, null documentation, and economics."""

    tables = _tables(source)
    documentation = _documentation(source).lower()
    violations: list[InvariantViolation] = []

    for claim in baseline_only_document_claims(source):
        violations.append(
            _violation(
                "document:injection-status-claim",
                f"the model-visible source document asserts injection status "
                f"({claim!r}); the shared document must not claim whether a "
                f"scenario was injected",
            )
        )

    for invariant in spec.keys:
        invariant_id = f"key:{invariant.table}:{','.join(invariant.columns)}"
        missing = _columns_present(
            tables, invariant.table, invariant.columns, invariant_id
        )
        if missing is not None:
            violations.append(missing)
            continue
        frame = tables[invariant.table]
        if frame[list(invariant.columns)].isna().any().any():
            violations.append(_violation(invariant_id, "key columns contain nulls"))
        if frame.duplicated(list(invariant.columns)).any():
            violations.append(_violation(invariant_id, "key values are not unique"))

    for invariant in spec.dates:
        invariant_id = f"date:{invariant.table}:{invariant.column}"
        missing = _columns_present(
            tables, invariant.table, (invariant.column,), invariant_id
        )
        if missing is not None:
            violations.append(missing)
            continue
        values = pd.to_datetime(
            tables[invariant.table][invariant.column],
            errors="coerce",
            format="mixed",
        )
        if values.isna().any():
            violations.append(
                _violation(invariant_id, "date values are null or invalid")
            )
            continue
        if invariant.minimum is not None and (values.dt.date < invariant.minimum).any():
            violations.append(
                _violation(
                    invariant_id, f"date is before {invariant.minimum.isoformat()}"
                )
            )
        if invariant.maximum is not None and (values.dt.date > invariant.maximum).any():
            violations.append(
                _violation(
                    invariant_id, f"date is after {invariant.maximum.isoformat()}"
                )
            )

    for invariant in spec.foreign_keys:
        invariant_id = (
            f"foreign_key:{invariant.child_table}:{','.join(invariant.child_columns)}"
        )
        missing = _columns_present(
            tables,
            invariant.child_table,
            invariant.child_columns,
            invariant_id,
        ) or _columns_present(
            tables,
            invariant.parent_table,
            invariant.parent_columns,
            invariant_id,
        )
        if missing is not None:
            violations.append(missing)
            continue
        child = tables[invariant.child_table]
        parent = tables[invariant.parent_table]
        child_values = child[list(invariant.child_columns)]
        if invariant.nullable:
            child_values = child_values.dropna(how="all")
        elif child_values.isna().any().any():
            violations.append(
                _violation(invariant_id, "foreign-key columns contain nulls")
            )
        parent_keys = set(map(tuple, parent[list(invariant.parent_columns)].to_numpy()))
        child_keys = set(map(tuple, child_values.to_numpy()))
        missing_keys = child_keys - parent_keys
        if missing_keys:
            violations.append(
                _violation(
                    invariant_id,
                    f"references absent parent keys: {sorted(missing_keys)[:3]}",
                )
            )

    for invariant in spec.date_relations:
        invariant_id = (
            f"date_relation:{invariant.child_table}:{invariant.child_date_column}"
        )
        missing = _columns_present(
            tables,
            invariant.child_table,
            (invariant.child_key, invariant.child_date_column),
            invariant_id,
        ) or _columns_present(
            tables,
            invariant.parent_table,
            (invariant.parent_key, invariant.parent_date_column),
            invariant_id,
        )
        if missing is not None:
            violations.append(missing)
            continue
        child = tables[invariant.child_table]
        parent = tables[invariant.parent_table]
        if parent[invariant.parent_key].duplicated().any():
            violations.append(
                _violation(
                    invariant_id,
                    f"parent key {invariant.parent_key!r} is not unique",
                )
            )
            continue
        parent_dates = parent.set_index(invariant.parent_key)[
            invariant.parent_date_column
        ]
        child_dates = pd.to_datetime(
            child[invariant.child_date_column], errors="coerce", format="mixed"
        )
        expected_dates = pd.to_datetime(
            child[invariant.child_key].map(parent_dates),
            errors="coerce",
            format="mixed",
        )
        if expected_dates.isna().any() or child_dates.isna().any():
            violations.append(
                _violation(invariant_id, "date relation contains missing dates")
            )
            continue
        if invariant.relation == "ge" and (child_dates < expected_dates).any():
            violations.append(
                _violation(invariant_id, "child date precedes parent date")
            )
        elif invariant.relation == "le" and (child_dates > expected_dates).any():
            violations.append(
                _violation(invariant_id, "child date follows parent date")
            )
        elif invariant.relation not in {"ge", "le"}:
            violations.append(
                _violation(invariant_id, f"unknown relation {invariant.relation!r}")
            )

    for invariant in spec.documented_nulls:
        invariant_id = f"documented_null:{invariant.table}:{invariant.column}"
        missing = _columns_present(
            tables, invariant.table, (invariant.column,), invariant_id
        )
        if missing is not None:
            violations.append(missing)
            continue
        frame = tables[invariant.table]
        try:
            allowed = invariant.allowed_when(frame)
            allowed_mask = (
                (
                    allowed
                    if isinstance(allowed, pd.Series)
                    else pd.Series(allowed, index=frame.index)
                )
                .fillna(False)
                .astype(bool)
            )
            null_mask = frame[invariant.column].isna()
            if (null_mask & ~allowed_mask).any():
                violations.append(
                    _violation(invariant_id, "undocumented null values are present")
                )
        except (KeyError, TypeError, ValueError) as exc:
            violations.append(_violation(invariant_id, f"null rule failed: {exc}"))
        missing_terms = [
            term
            for term in invariant.documentation_terms
            if term.lower() not in documentation
        ]
        if missing_terms:
            violations.append(
                _violation(
                    invariant_id,
                    "documentation omits allowed-null terms: "
                    + ", ".join(missing_terms),
                )
            )

    for invariant in spec.economic_identities:
        invariant_id = f"economic:{invariant.invariant_id}"
        required = (invariant.result_column, *invariant.operand_columns)
        missing = _columns_present(tables, invariant.table, required, invariant_id)
        if missing is not None:
            violations.append(missing)
            continue
        frame = tables[invariant.table]
        try:
            expected = invariant.expected(frame)
            actual = frame[invariant.result_column]
            if not np.allclose(actual, expected, atol=invariant.tolerance, rtol=0):
                violations.append(
                    _violation(invariant_id, "economic identity does not reconcile")
                )
        except (TypeError, ValueError) as exc:
            violations.append(
                _violation(invariant_id, f"economic identity failed: {exc}")
            )
        missing_terms = [
            term
            for term in invariant.documentation_terms
            if term.lower() not in documentation
        ]
        if missing_terms:
            violations.append(
                _violation(
                    invariant_id,
                    "documentation omits identity terms: " + ", ".join(missing_terms),
                )
            )

    for invariant in spec.row_invariants:
        missing = _columns_present(tables, invariant.table, (), invariant.invariant_id)
        if missing is not None:
            violations.append(missing)
            continue
        try:
            result = invariant.predicate(tables[invariant.table])
            passed = (
                bool(result.all()) if isinstance(result, pd.Series) else bool(result)
            )
            if not passed:
                violations.append(
                    _violation(invariant.invariant_id, invariant.description)
                )
        except (KeyError, TypeError, ValueError) as exc:
            violations.append(
                _violation(invariant.invariant_id, f"row rule failed: {exc}")
            )

    return tuple(violations)


def check_metric_identities(
    metrics: Sequence[GroundTruthMetric],
) -> tuple[InvariantViolation, ...]:
    """Reject ambiguous evaluator metric identities before generation."""

    violations: list[InvariantViolation] = []
    ids = [metric.id for metric in metrics]
    comparisons = [metric.comparison for metric in metrics]
    if len(set(ids)) != len(ids):
        violations.append(
            _violation("metric_identity:ids", "metric IDs are not unique")
        )
    if len(set(comparisons)) != len(comparisons):
        violations.append(
            _violation("metric_identity:comparisons", "comparison IDs are not unique")
        )
    identities: list[tuple[object, ...]] = []
    for metric in metrics:
        if normalize_metric_period(metric.baseline_period) == normalize_metric_period(
            metric.comparison_period
        ):
            violations.append(
                _violation(
                    f"metric_identity:{metric.id}",
                    "baseline and comparison periods are identical",
                )
            )
        identity = (
            normalize_metric_key(metric.metric_key, metric.dimensions),
            tuple(sorted(normalized_dimension_mapping(metric.dimensions).items())),
            normalize_metric_period(metric.baseline_period),
            normalize_metric_period(metric.comparison_period),
            metric.comparison_type.value,
            normalize_metric_unit(metric.value_unit, metric.comparison_type),
            tuple(
                sorted(
                    (key, value.lower())
                    for key, value in (
                        metric.definition_context.model_dump(exclude_none=True).items()
                        if metric.definition_context is not None
                        else {}
                    )
                )
            ),
        )
        identities.append(identity)
        if not isfinite(metric.expected_relative_change) or not isfinite(
            metric.tolerance
        ):
            violations.append(
                _violation(
                    f"metric_identity:{metric.id}", "metric values must be finite"
                )
            )
    if len(set(identities)) != len(identities):
        violations.append(
            _violation("metric_identity:estimands", "metric estimands are duplicated")
        )
    return tuple(violations)


def check_observable_ground_truth(
    observed: Sequence[MetricComparison],
    expected: Sequence[GroundTruthMetric],
) -> tuple[InvariantViolation, ...]:
    """Compare generated, observable measurements with evaluator ground truth."""

    violations: list[InvariantViolation] = []
    normalized_observed = [normalize_metric_comparison(item) for item in observed]
    for metric in expected:
        expected_key = normalize_metric_key(metric.metric_key, metric.dimensions)
        expected_dimensions = {
            key.lower(): value.lower()
            for key, value in normalized_dimension_mapping(metric.dimensions).items()
        }
        matches = [
            item
            for item in normalized_observed
            if item.metric_key == expected_key
            and {
                dimension.name.lower(): dimension.value.lower()
                for dimension in item.dimensions
            }
            == expected_dimensions
            and normalize_metric_period(item.baseline_period)
            == normalize_metric_period(metric.baseline_period)
            and normalize_metric_period(item.comparison_period)
            == normalize_metric_period(metric.comparison_period)
            and item.comparison_type is metric.comparison_type
            and item.unit
            == normalize_metric_unit(metric.value_unit, metric.comparison_type)
            and metric_definition_contexts_match(
                item.definition_context,
                metric.definition_context,
            )
        ]
        invariant_id = f"ground_truth:{metric.id}"
        if not matches:
            violations.append(_violation(invariant_id, "observable metric is missing"))
        elif len(matches) > 1:
            violations.append(
                _violation(invariant_id, "observable metric is ambiguous")
            )
        elif abs(matches[0].value - metric.expected_relative_change) > metric.tolerance:
            violations.append(
                _violation(
                    invariant_id,
                    f"observed {matches[0].value} outside expected "
                    f"{metric.expected_relative_change} +/- {metric.tolerance}",
                )
            )
    return tuple(violations)


@dataclass(frozen=True, slots=True)
class ScenarioInvariantSuite:
    """Reusable base invariants plus optional scenario ground-truth probes."""

    dataset_spec: DatasetInvariantSpec
    expected_metrics: tuple[GroundTruthMetric, ...] = ()
    metric_observer: MetricObserver | None = None

    def validate(self, source: object | TableMap) -> InvariantReport:
        """Run all source, metric-identity, and observable-truth checks."""

        violations = list(check_dataset_invariants(source, self.dataset_spec))
        violations.extend(check_metric_identities(self.expected_metrics))
        if self.expected_metrics and self.metric_observer is None:
            violations.append(
                _violation(
                    "ground_truth:observer",
                    "expected metrics have no observable metric probe",
                )
            )
        elif self.metric_observer is not None:
            try:
                observed = self.metric_observer(source)
                violations.extend(
                    check_observable_ground_truth(observed, self.expected_metrics)
                )
            except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
                violations.append(_violation("ground_truth:observer", str(exc)))
        return InvariantReport(tuple(violations))


def synthetic_ecommerce_invariant_suite(
    *,
    expected_metrics: Sequence[GroundTruthMetric] = (),
    metric_observer: MetricObserver | None = None,
) -> ScenarioInvariantSuite:
    """Return common invariants for the clean ecommerce source family."""

    return ScenarioInvariantSuite(
        dataset_spec=DatasetInvariantSpec(
            keys=(
                KeyInvariant("customers", ("customer_id",)),
                KeyInvariant("orders", ("order_id",)),
                KeyInvariant("sessions", ("session_id",)),
                KeyInvariant("marketing_spend", ("date", "channel")),
            ),
            dates=(
                DateInvariant("customers", "acquisition_date"),
                DateInvariant("orders", "order_date"),
                DateInvariant("sessions", "session_date"),
                DateInvariant("marketing_spend", "date"),
            ),
            foreign_keys=(
                ForeignKeyInvariant(
                    "orders", ("customer_id",), "customers", ("customer_id",)
                ),
                ForeignKeyInvariant(
                    "sessions",
                    ("customer_id",),
                    "customers",
                    ("customer_id",),
                    nullable=True,
                ),
            ),
            date_relations=(
                DateRelationInvariant(
                    "orders",
                    "order_date",
                    "customers",
                    "customer_id",
                    "acquisition_date",
                    "customer_id",
                ),
            ),
            documented_nulls=(
                DocumentedNullInvariant(
                    "sessions",
                    "customer_id",
                    allowed_when=lambda frame: ~frame["converted"].astype(bool),
                    documentation_terms=("customer_id = null", "non-converting"),
                ),
            ),
            economic_identities=(
                EconomicIdentityInvariant(
                    "orders.net_revenue",
                    "orders",
                    "net_revenue",
                    ("gross_revenue", "discount", "refund"),
                    expected=lambda frame: (
                        frame["gross_revenue"] - frame["discount"] - frame["refund"]
                    ).round(2),
                    documentation_terms=(
                        "net revenue",
                        "gross_revenue - discount - refund",
                    ),
                ),
            ),
            row_invariants=(
                RowInvariant(
                    "sessions:conversion-null-contract",
                    "sessions",
                    lambda frame: (
                        frame.loc[frame["converted"].astype(bool), "customer_id"]
                        .notna()
                        .all()
                        and frame.loc[~frame["converted"].astype(bool), "customer_id"]
                        .isna()
                        .all()
                    ),
                    "converted sessions must have customers and non-converting "
                    "sessions must be null",
                ),
                RowInvariant(
                    "orders:cogs-below-net-revenue",
                    "orders",
                    lambda frame: (frame["cogs"] < frame["net_revenue"]).all(),
                    "order COGS must remain below net revenue",
                ),
                RowInvariant(
                    "marketing:clicks-within-impressions",
                    "marketing_spend",
                    lambda frame: (frame["clicks"] <= frame["impressions"]).all(),
                    "clicks must not exceed impressions",
                ),
            ),
        ),
        expected_metrics=tuple(expected_metrics),
        metric_observer=metric_observer,
    )


def experiment_invariant_suite(
    *,
    expected_metrics: Sequence[GroundTruthMetric] = (),
    metric_observer: MetricObserver | None = None,
) -> ScenarioInvariantSuite:
    """Return common invariants for the deterministic two-arm experiment source."""

    return ScenarioInvariantSuite(
        dataset_spec=DatasetInvariantSpec(
            keys=(KeyInvariant("experiment_observations", ("subject_id",)),),
            row_invariants=(
                RowInvariant(
                    "experiment:assignments",
                    "experiment_observations",
                    lambda frame: set(frame["assignment"]) == {"control", "treatment"},
                    "experiment must contain exactly control and treatment arms",
                ),
                RowInvariant(
                    "experiment:binary-outcomes",
                    "experiment_observations",
                    lambda frame: set(frame["outcome"]).issubset({0, 1}),
                    "experiment outcomes must be binary",
                ),
                RowInvariant(
                    "experiment:one-observation-per-subject",
                    "experiment_observations",
                    lambda frame: not frame["subject_id"].duplicated().any(),
                    "each subject must contribute one observation",
                ),
            ),
        ),
        expected_metrics=tuple(expected_metrics),
        metric_observer=metric_observer,
    )


def validate_synthetic_ecommerce_baseline(source: object | TableMap) -> InvariantReport:
    """Validate a clean ecommerce dataset independently of any scenario."""

    return synthetic_ecommerce_invariant_suite().validate(source)


__all__ = [
    "BASELINE_ONLY_DOCUMENT_CLAIMS",
    "DatasetInvariantSpec",
    "DateInvariant",
    "DateRelationInvariant",
    "DocumentedNullInvariant",
    "EconomicIdentityInvariant",
    "ForeignKeyInvariant",
    "InvariantReport",
    "InvariantViolation",
    "KeyInvariant",
    "RowInvariant",
    "ScenarioInvariantError",
    "ScenarioInvariantSuite",
    "baseline_only_document_claims",
    "check_dataset_invariants",
    "check_metric_identities",
    "check_observable_ground_truth",
    "experiment_invariant_suite",
    "synthetic_ecommerce_invariant_suite",
    "validate_synthetic_ecommerce_baseline",
]
