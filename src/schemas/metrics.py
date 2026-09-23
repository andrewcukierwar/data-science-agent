"""Generic typed metric observations used across business-analysis tasks."""

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from math import isclose, isfinite
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas.common import NonEmptyString
from schemas.computation import BoundNumericalClaim


class MetricComparisonType(StrEnum):
    """The numerical relationship represented by a metric comparison."""

    LEVEL = "level"
    ABSOLUTE_DIFFERENCE = "absolute_difference"
    RELATIVE_CHANGE = "relative_change"


class MetricDimension(BaseModel):
    """One named segment dimension attached to a metric measurement.

    Segment dimensions are analytically open-ended (channel, cohort, device,
    experiment arm, ...), but a strict JSON Schema cannot express dynamic
    object keys. Naming the dimension in a typed field keeps the estimand
    identical while giving every analytical agent a strict output contract.
    """

    model_config = ConfigDict(extra="forbid")

    name: NonEmptyString
    value: NonEmptyString


def coerce_metric_dimensions(value: Any) -> Any:
    """Accept the strict typed list or the legacy persisted mapping form.

    The strict wire contract is a list of ``MetricDimension`` objects. Run
    ledgers and benchmark records written before R13 persisted an open-ended
    ``{name: value}`` mapping, so offline evaluation of retained evidence must
    still load them. Both forms produce exactly the same typed dimensions.
    """

    if isinstance(value, Mapping):
        return [
            {"name": name, "value": dimension_value}
            for name, dimension_value in value.items()
        ]
    return value


def _validate_unique_dimension_names(
    dimensions: list[MetricDimension],
) -> list[MetricDimension]:
    """Reject ambiguous repeated dimension names in one measurement."""

    seen: set[str] = set()
    for dimension in dimensions:
        identity = _slug(dimension.name)
        if identity in seen:
            raise ValueError(f"duplicate metric dimension name: {dimension.name}")
        seen.add(identity)
    return dimensions


MetricDimensions = Annotated[
    list[MetricDimension],
    Field(default_factory=list),
]


class _DimensionedModel(BaseModel):
    """Shared strict validation for typed segment dimensions."""

    @field_validator("dimensions", mode="before", check_fields=False)
    @classmethod
    def accept_legacy_dimension_mapping(cls, value: Any) -> Any:
        return coerce_metric_dimensions(value)

    @field_validator("dimensions", check_fields=False)
    @classmethod
    def dimension_names_are_unique(
        cls,
        value: list[MetricDimension],
    ) -> list[MetricDimension]:
        return _validate_unique_dimension_names(value)


class MetricObservation(_DimensionedModel):
    """One metric value for a period and optional segment dimensions."""

    model_config = ConfigDict(extra="forbid")

    metric_key: NonEmptyString
    dimensions: MetricDimensions
    period: NonEmptyString
    value: float
    unit: NonEmptyString
    evidence_refs: list[NonEmptyString] = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def value_must_be_finite(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("metric value must be finite")
        return value


class MetricDefinitionContext(BaseModel):
    """Scope needed to identify the estimand behind a metric value."""

    model_config = ConfigDict(extra="forbid")

    population: NonEmptyString | None = None
    date_basis: NonEmptyString | None = None
    observation_window: NonEmptyString | None = None
    numerator: NonEmptyString | None = None
    denominator: NonEmptyString | None = None
    definition_ref: NonEmptyString | None = None


class MetricComparison(_DimensionedModel, BoundNumericalClaim):
    """A reproducible period/segment comparison supporting a conclusion."""

    model_config = ConfigDict(extra="forbid")

    numerical_fields = ("value",)

    metric_key: NonEmptyString
    dimensions: MetricDimensions
    baseline_period: NonEmptyString
    comparison_period: NonEmptyString
    comparison_type: MetricComparisonType
    value: float | None = None
    unit: NonEmptyString
    evidence_refs: list[NonEmptyString] = Field(min_length=1)
    definition_context: MetricDefinitionContext | None = None

    @field_validator("value")
    @classmethod
    def value_must_be_finite(cls, value: float | None) -> float | None:
        if value is not None and not isfinite(value):
            raise ValueError("metric value must be finite")
        return value


class MetricConflict(_DimensionedModel):
    """Materially different values reported for one analytical estimand."""

    model_config = ConfigDict(extra="forbid")

    metric_key: NonEmptyString
    dimensions: MetricDimensions
    baseline_period: NonEmptyString
    comparison_period: NonEmptyString
    comparison_type: MetricComparisonType
    unit: NonEmptyString
    comparisons: list[MetricComparison] = Field(min_length=2)


class MetricCompilationResult(BaseModel):
    """One canonical final metric set plus observable material conflicts."""

    model_config = ConfigDict(extra="forbid")

    comparisons: list[MetricComparison] = Field(default_factory=list)
    conflicts: list[MetricConflict] = Field(default_factory=list)


_METRIC_ALIASES = {
    "conversion": "conversion_rate",
    "session_conversion": "conversion_rate",
    "session_conversion_rate": "conversion_rate",
    "spend": "marketing_spend",
    "marketing": "marketing_spend",
    "customer_acquisition_cost": "cac",
    "overall_cac": "cac",
    "new_customers": "acquired_customers",
    "customer_count": "acquired_customers",
    "acquired_customer_count": "acquired_customers",
    "acquisition_sessions": "sessions",
    "all_channel_acquisition_sessions": "sessions",
    "session_count": "sessions",
    "traffic": "sessions",
    "ltv_90d": "ltv",
    "ltv_90_day": "ltv",
    "90d_ltv": "ltv",
    "90_day_ltv": "ltv",
    "acquired_customer_90d_ltv": "ltv",
    "acquired_customer_90_day_ltv": "ltv",
}
_DIMENSION_ALIASES = {
    "acquisition_channel": "channel",
    "channel_name": "channel",
    "segment_name": "segment",
}
_RELATIVE_FRACTION_UNITS = {
    "fraction",
    "decimal_fraction",
    "relative_fraction",
    "relative_change",
    "relative_change_decimal",
    "relative_change_fraction",
}


def _slug(value: str) -> str:
    """Create a stable generic identifier from a model-provided label."""

    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def dimension_mapping(
    dimensions: Sequence[MetricDimension],
) -> dict[str, str]:
    """Return the ``{name: value}`` view used for deterministic comparisons.

    Later entries win, matching the mapping form these dimensions previously
    used, so the estimand seen by the evaluator is unchanged.
    """

    return {dimension.name: dimension.value for dimension in dimensions}


def normalize_metric_dimensions(
    dimensions: Sequence[MetricDimension] | Mapping[str, str],
) -> list[MetricDimension]:
    """Normalize generic dimension names without changing their values.

    The result is sorted by normalized name so an equivalent measurement always
    round-trips to exactly one canonical typed representation.
    """

    coerced = [
        item
        if isinstance(item, MetricDimension)
        else MetricDimension.model_validate(item)
        for item in coerce_metric_dimensions(dimensions)
    ]
    normalized: dict[str, str] = {}
    for dimension in coerced:
        normalized_name = _DIMENSION_ALIASES.get(
            _slug(dimension.name), _slug(dimension.name)
        )
        normalized[normalized_name] = dimension.value.strip()
    return [
        MetricDimension(name=name, value=value)
        for name, value in sorted(normalized.items())
    ]


def normalized_dimension_mapping(
    dimensions: Sequence[MetricDimension] | Mapping[str, str],
) -> dict[str, str]:
    """Return the normalized dimensions as a deterministic mapping."""

    return dimension_mapping(normalize_metric_dimensions(dimensions))


def normalize_metric_key(
    metric_key: str,
    dimensions: Sequence[MetricDimension] | Mapping[str, str],
    *,
    legacy_contract: bool = False,
) -> str:
    """Normalize aliases and remove redundant dimension-value prefixes.

    A metric key names the measure only. For example, ``meta_cac`` with a
    ``channel=Meta`` dimension becomes ``cac``. The transformation is generic
    and does not know about any scenario-specific metric IDs.
    """

    normalized_key = _slug(metric_key)
    normalized_dimensions = normalize_metric_dimensions(dimensions)
    prefixes = sorted(
        {
            _slug(dimension.value)
            for dimension in normalized_dimensions
            if _slug(dimension.value)
        },
        key=len,
        reverse=True,
    )
    for prefix in prefixes:
        marker = f"{prefix}_"
        if normalized_key.startswith(marker):
            normalized_key = normalized_key[len(marker) :]
            break
    key_terms = set(normalized_key.split("_"))
    rate_difference_terms = {
        "binary",
        "outcome",
        "conversion",
        "rate",
        "proportion",
        "difference",
    }
    conversion_effect_terms = {
        "experiment",
        "binary",
        "outcome",
        "conversion",
        "effect",
    }
    if not legacy_contract and (
        (
            {"difference"} <= key_terms
            and bool(key_terms & {"rate", "proportion"})
            and key_terms <= rate_difference_terms
        )
        or (
            {"conversion", "effect"} <= key_terms
            and key_terms <= conversion_effect_terms
        )
    ):
        return "rate_difference"
    return _METRIC_ALIASES.get(normalized_key, normalized_key)


def normalize_metric_period(period: str, *, legacy_contract: bool = False) -> str:
    """Normalize common quarter labels while preserving generic periods."""

    normalized = re.sub(r"[-_/]+", " ", period.strip().lower())
    normalized = re.sub(r"\s+", " ", normalized)
    match = re.fullmatch(r"(?:q([1-4])\s*(\d{4})|(\d{4})\s*q([1-4]))", normalized)
    if match:
        quarter = match.group(1) or match.group(4)
        year = match.group(2) or match.group(3)
        return f"Q{quarter} {year}"
    if legacy_contract:
        return normalized
    role_label = re.sub(
        r"\b(?:arm|group|participants|participant|subjects)\b", " ", normalized
    )
    role_label = re.sub(r"\s+", " ", role_label).strip()
    if role_label in {"control", "control arm", "control group"}:
        return "control"
    if role_label in {"treatment", "treatment arm", "treatment group"}:
        return "treatment"
    return normalized


def normalize_metric_unit(
    unit: str,
    comparison_type: MetricComparisonType,
    *,
    legacy_contract: bool = False,
) -> str:
    """Normalize units only when the numeric interpretation is unchanged."""

    normalized = _slug(unit)
    if comparison_type is MetricComparisonType.RELATIVE_CHANGE and (
        normalized in _RELATIVE_FRACTION_UNITS
        or normalized.endswith("_relative_change_fraction")
    ):
        return "relative_change_fraction"
    if (
        not legacy_contract
        and comparison_type
        in {
            MetricComparisonType.LEVEL,
            MetricComparisonType.ABSOLUTE_DIFFERENCE,
        }
        and normalized in {"fraction", "proportion", "rate", "decimal_fraction"}
    ):
        return "fraction"
    return normalized


def normalize_metric_definition_context(
    context: MetricDefinitionContext | None,
) -> MetricDefinitionContext | None:
    """Normalize scope labels without changing their analytical meaning."""

    if context is None:
        return None
    normalized = context.model_copy(
        update={
            field_name: (
                value.strip() if isinstance(value, str) and value.strip() else None
            )
            for field_name, value in context.model_dump().items()
        }
    )
    return (
        normalized
        if any(value is not None for value in normalized.model_dump().values())
        else None
    )


def normalize_metric_comparison(
    comparison: MetricComparison,
    *,
    legacy_contract: bool = False,
) -> MetricComparison:
    """Return the canonical application-boundary form of a comparison."""

    dimensions = normalize_metric_dimensions(comparison.dimensions)
    definition_context = normalize_metric_definition_context(
        comparison.definition_context
    )
    return comparison.model_copy(
        update={
            "metric_key": normalize_metric_key(
                comparison.metric_key,
                dimensions,
                legacy_contract=legacy_contract,
            ),
            "dimensions": dimensions,
            "baseline_period": normalize_metric_period(
                comparison.baseline_period, legacy_contract=legacy_contract
            ),
            "comparison_period": normalize_metric_period(
                comparison.comparison_period, legacy_contract=legacy_contract
            ),
            "unit": normalize_metric_unit(
                comparison.unit,
                comparison.comparison_type,
                legacy_contract=legacy_contract,
            ),
            "definition_context": definition_context,
        }
    )


def metric_comparison_scope_identity(
    comparison: MetricComparison,
    *,
    legacy_contract: bool = False,
) -> tuple[object, ...]:
    """Return identity without definition context for scope-mismatch checks."""

    comparison = normalize_metric_comparison(
        comparison, legacy_contract=legacy_contract
    )
    return (
        comparison.metric_key,
        tuple(
            sorted(
                (dimension.name, dimension.value.strip().lower())
                for dimension in comparison.dimensions
            )
        ),
        comparison.baseline_period,
        comparison.comparison_period,
        comparison.comparison_type.value,
        comparison.unit,
    )


def metric_comparison_identity(
    comparison: MetricComparison,
    *,
    legacy_contract: bool = False,
) -> tuple[object, ...]:
    """Return the stable identity of a metric comparison, excluding its value."""

    comparison = normalize_metric_comparison(
        comparison, legacy_contract=legacy_contract
    )
    context = comparison.definition_context
    context_identity = (
        tuple(
            sorted(
                (key, value.strip().lower())
                for key, value in context.model_dump().items()
                if value is not None
            )
        )
        if context is not None
        else None
    )
    return (*metric_comparison_scope_identity(comparison), context_identity)


_CONTEXT_ANCHORS = {
    "population": {
        "acquisition_cohort": (
            "acquisition cohort",
            "acquired customer",
            "new customer",
        ),
        "orders": ("orders", "order rows"),
        "sessions": ("sessions", "session rows"),
    },
    "date_basis": {
        "acquisition_date": ("acquisition_date", "acquisition date"),
        "order_date": ("order_date", "order date"),
        "session_date": ("session_date", "session date"),
        "marketing_date": ("marketing spend date", "spend date"),
    },
    "observation_window": {
        "90_day": ("90 day", "90-day", "90d"),
        "calendar_period": ("calendar quarter", "calendar period", "reporting period"),
        "lifetime": ("lifetime", "all available history"),
    },
    "numerator": {
        "marketing_spend": ("marketing spend", "ad spend"),
        "converted_sessions": ("converted sessions", "conversions"),
        "acquired_customers": ("acquired customers", "new customers"),
        "retained_customers": (
            "retained customers",
            "repeat customers",
            "second order",
        ),
        "net_revenue": ("net revenue",),
        "cogs": ("cogs", "cost of goods"),
        "discount": ("discount", "discounts"),
        "refund": ("refund", "refunds"),
        "contribution": ("contribution",),
    },
    "denominator": {
        "sessions": ("sessions", "traffic"),
        "acquired_customers": ("acquired customers", "new customers"),
        "orders": ("orders",),
        "net_revenue": ("net revenue",),
        "gross_revenue": ("gross revenue", "gross sales"),
    },
}


def _context_anchor_sets(
    context: MetricDefinitionContext | None,
) -> dict[str, frozenset[str]]:
    """Extract only scope distinctions that can change an estimand."""

    if context is None:
        return {}
    anchors: dict[str, frozenset[str]] = {}
    for field_name, candidates in _CONTEXT_ANCHORS.items():
        value = getattr(context, field_name)
        if value is None:
            continue
        normalized = value.lower().replace("_", " ")
        matched = frozenset(
            anchor
            for anchor, terms in candidates.items()
            if any(term.replace("_", " ") in normalized for term in terms)
        )
        if matched:
            anchors[field_name] = matched
    return anchors


def _normalize_context_value(field_name: str, value: str) -> str:
    """Canonicalize only common wording variants of structured estimand fields."""

    normalized = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    tokens = set(normalized.split())
    if field_name == "population":
        if normalized in {"acquired customer cohort", "acquisition customer cohort"}:
            return "acquired customer cohort"
        if normalized in {
            "randomized experiment participants",
            "participants randomized to the experiment",
        }:
            return "randomized experiment participants"
    if field_name == "date_basis":
        if normalized in {"assignment", "random assignment date"}:
            return "assignment"
        bases = []
        if "assignment" in tokens:
            bases.append("assignment")
        if "acquisition" in tokens:
            bases.append("acquisition_date")
        if "order" in tokens:
            bases.append("order_date")
        if "session" in tokens:
            bases.append("session_date")
        if "marketing" in tokens or "spend" in tokens:
            bases.append("marketing_date")
        if bases and not (
            tokens
            - {
                "acquisition",
                "date",
                "cohort",
                "and",
                "order",
                "observation",
                "session",
                "marketing",
                "spend",
            }
        ):
            return ",".join(sorted(set(bases)))
    if field_name == "observation_window":
        if normalized in {"90 day", "90 days", "90d"}:
            return "90_day"
        if normalized in {"experiment enrollment", "experiment enrollment window"}:
            return "experiment_enrollment"
        if normalized in {"calendar quarter", "calendar period"}:
            return "calendar_period"
        if normalized in {"lifetime", "history"}:
            return "lifetime"
    if field_name == "denominator" and normalized in {
        "assigned participants",
        "all randomly assigned participants",
    }:
        return "assigned participants"
    aliases = {
        "numerator": (
            ({"marketing", "spend"}, "marketing spend"),
            ({"ad", "spend"}, "marketing spend"),
            ({"acquired", "customers"}, "acquired customers"),
            ({"new", "customers"}, "acquired customers"),
            ({"retained", "customers"}, "retained customers"),
            ({"repeat", "customers"}, "retained customers"),
            ({"second", "order"}, "retained customers"),
            ({"successful", "outcomes"}, "successful outcomes"),
            ({"binary", "successes"}, "successful outcomes"),
            ({"conversions"}, "successful outcomes"),
            ({"cogs"}, "cogs"),
            ({"discount"}, "discount"),
            ({"refund"}, "refund"),
        ),
        "denominator": (
            ({"assigned", "participants"}, "assigned participants"),
            ({"participants", "assigned"}, "assigned participants"),
            ({"sessions"}, "sessions"),
            ({"traffic"}, "sessions"),
            ({"acquired", "customers"}, "acquired customers"),
            ({"new", "customers"}, "acquired customers"),
            ({"orders"}, "orders"),
            ({"gross", "revenue"}, "gross revenue"),
            ({"net", "revenue"}, "net revenue"),
        ),
    }
    for required_tokens, canonical in aliases.get(field_name, ()):
        if required_tokens == tokens:
            return canonical
    return normalized


def metric_definition_contexts_compatible(
    left: MetricDefinitionContext | None,
    right: MetricDefinitionContext | None,
) -> bool:
    """Return whether two contexts can describe the same analytical estimand.

    Free-form wording is intentionally not identity. Only incompatible scope
    anchors (for example acquisition-date cohorts versus calendar order dates)
    split otherwise equivalent measurements.
    """

    left_anchors = _context_anchor_sets(left)
    right_anchors = _context_anchor_sets(right)
    for field_name in _CONTEXT_ANCHORS:
        left_values = left_anchors.get(field_name)
        right_values = right_anchors.get(field_name)
        if left_values and right_values and left_values.isdisjoint(right_values):
            return False
    return True


def metric_definition_contexts_match(
    actual: MetricDefinitionContext | None,
    expected: MetricDefinitionContext | None,
    *,
    legacy_contract: bool = False,
) -> bool:
    """Require an actual comparison to carry every expected estimand anchor."""

    if expected is None:
        return True
    if actual is None or not metric_definition_contexts_compatible(actual, expected):
        return False
    if legacy_contract:
        expected_anchors = _context_anchor_sets(expected)
        actual_anchors = _context_anchor_sets(actual)
        return all(
            field_name in actual_anchors
            and expected_values.issubset(actual_anchors[field_name])
            for field_name, expected_values in expected_anchors.items()
        )
    for field_name in (
        "population",
        "date_basis",
        "observation_window",
        "numerator",
        "denominator",
    ):
        expected_value = getattr(expected, field_name)
        actual_value = getattr(actual, field_name)
        if expected_value is None:
            if actual_value is not None:
                return False
            continue
        if actual_value is None or _normalize_context_value(
            field_name, actual_value
        ) != _normalize_context_value(field_name, expected_value):
            return False
    return True


def _merge_evidence_refs(comparisons: list[MetricComparison]) -> list[str]:
    return list(
        dict.fromkeys(
            reference
            for comparison in comparisons
            for reference in comparison.evidence_refs
        )
    )


def _best_definition_context(
    comparisons: list[MetricComparison],
) -> MetricDefinitionContext | None:
    """Prefer the latest most-specific compatible context."""

    candidates = [
        comparison.definition_context
        for comparison in comparisons
        if comparison.definition_context is not None
    ]
    if not candidates:
        return None
    return max(
        enumerate(candidates),
        key=lambda item: (
            sum(value is not None for value in item[1].model_dump().values()),
            item[0],
        ),
    )[1]


def compile_metric_comparisons(
    comparisons: list[MetricComparison],
    *,
    relative_tolerance: float = 1e-3,
    absolute_tolerance: float = 1e-3,
    legacy_contract: bool = False,
) -> MetricCompilationResult:
    """Compile working measurements into one deterministic final metric set.

    Equivalent, numerically consistent measurements corroborate one another and
    merge provenance. Materially inconsistent measurements remain represented
    once in the final set and produce an explicit conflict for Critic review.
    Definition scopes that identify different estimands are never merged.
    """

    groups: list[list[MetricComparison]] = []
    for raw_comparison in comparisons:
        comparison = normalize_metric_comparison(
            raw_comparison, legacy_contract=legacy_contract
        )
        for group in groups:
            if metric_comparison_scope_identity(
                group[0], legacy_contract=legacy_contract
            ) == metric_comparison_scope_identity(
                comparison, legacy_contract=legacy_contract
            ) and all(
                metric_definition_contexts_compatible(
                    comparison.definition_context,
                    member.definition_context,
                )
                for member in group
            ):
                group.append(comparison)
                break
        else:
            groups.append([comparison])

    compiled: list[MetricComparison] = []
    conflicts: list[MetricConflict] = []
    for group in groups:
        latest = group[-1]
        consistent = all(
            isclose(
                left.value,
                right.value,
                rel_tol=relative_tolerance,
                abs_tol=absolute_tolerance,
            )
            for index, left in enumerate(group)
            for right in group[index + 1 :]
        )
        if consistent and latest.computation is None:
            latest = latest.model_copy(
                update={
                    "evidence_refs": _merge_evidence_refs(group),
                    "definition_context": _best_definition_context(group),
                }
            )
        elif not consistent:
            conflicts.append(
                MetricConflict(
                    metric_key=latest.metric_key,
                    dimensions=latest.dimensions,
                    baseline_period=latest.baseline_period,
                    comparison_period=latest.comparison_period,
                    comparison_type=latest.comparison_type,
                    unit=latest.unit,
                    comparisons=group,
                )
            )
        compiled.append(latest)

    return MetricCompilationResult(comparisons=compiled, conflicts=conflicts)


def deduplicate_metric_comparisons(
    comparisons: list[MetricComparison],
) -> list[MetricComparison]:
    """Keep the latest comparison for each deterministic generic identity.

    Later specialist/remediation output is authoritative for an equivalent
    identity, so a corrected comparison replaces a stale one deterministically.
    """

    deduplicated: dict[tuple[object, ...], MetricComparison] = {}
    for comparison in comparisons:
        comparison = normalize_metric_comparison(comparison)
        identity = metric_comparison_identity(comparison)
        if comparison.computation is not None:
            identity = (
                *identity,
                comparison.result_id,
                comparison.computation.model_dump_json(),
                comparison.value,
            )
        deduplicated[identity] = comparison
    return list(deduplicated.values())


__all__ = [
    "MetricCompilationResult",
    "MetricComparison",
    "MetricComparisonType",
    "MetricConflict",
    "MetricDefinitionContext",
    "MetricDimension",
    "MetricDimensions",
    "MetricObservation",
    "coerce_metric_dimensions",
    "compile_metric_comparisons",
    "deduplicate_metric_comparisons",
    "metric_comparison_identity",
    "metric_comparison_scope_identity",
    "metric_definition_contexts_compatible",
    "metric_definition_contexts_match",
    "normalize_metric_definition_context",
    "normalize_metric_comparison",
    "normalize_metric_dimensions",
    "normalize_metric_key",
    "normalized_dimension_mapping",
    "dimension_mapping",
    "normalize_metric_period",
    "normalize_metric_unit",
]
