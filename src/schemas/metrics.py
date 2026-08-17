"""Generic typed metric observations used across business-analysis tasks."""

import re
from enum import StrEnum
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas.common import NonEmptyString


class MetricComparisonType(StrEnum):
    """The numerical relationship represented by a metric comparison."""

    LEVEL = "level"
    ABSOLUTE_DIFFERENCE = "absolute_difference"
    RELATIVE_CHANGE = "relative_change"


class MetricObservation(BaseModel):
    """One metric value for a period and optional segment dimensions."""

    model_config = ConfigDict(extra="forbid")

    metric_key: NonEmptyString
    dimensions: dict[NonEmptyString, NonEmptyString] = Field(default_factory=dict)
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


class MetricComparison(BaseModel):
    """A reproducible period/segment comparison supporting a conclusion."""

    model_config = ConfigDict(extra="forbid")

    metric_key: NonEmptyString
    dimensions: dict[NonEmptyString, NonEmptyString] = Field(default_factory=dict)
    baseline_period: NonEmptyString
    comparison_period: NonEmptyString
    comparison_type: MetricComparisonType
    value: float
    unit: NonEmptyString
    evidence_refs: list[NonEmptyString] = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def value_must_be_finite(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("metric value must be finite")
        return value


_METRIC_ALIASES = {
    "conversion": "conversion_rate",
    "session_conversion": "conversion_rate",
    "session_conversion_rate": "conversion_rate",
    "spend": "marketing_spend",
    "marketing": "marketing_spend",
    "customer_acquisition_cost": "cac",
    "new_customers": "acquired_customers",
    "customer_count": "acquired_customers",
    "acquired_customer_count": "acquired_customers",
    "ltv_90d": "ltv",
    "ltv_90_day": "ltv",
    "90d_ltv": "ltv",
    "90_day_ltv": "ltv",
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


def normalize_metric_dimensions(
    dimensions: dict[str, str],
) -> dict[str, str]:
    """Normalize generic dimension names without changing their values."""

    normalized: dict[str, str] = {}
    for key, value in dimensions.items():
        normalized_key = _DIMENSION_ALIASES.get(_slug(key), _slug(key))
        normalized[normalized_key] = value.strip()
    return normalized


def normalize_metric_key(
    metric_key: str,
    dimensions: dict[str, str],
) -> str:
    """Normalize aliases and remove redundant dimension-value prefixes.

    A metric key names the measure only. For example, ``meta_cac`` with a
    ``channel=Meta`` dimension becomes ``cac``. The transformation is generic
    and does not know about any scenario-specific metric IDs.
    """

    normalized_key = _slug(metric_key)
    normalized_dimensions = normalize_metric_dimensions(dimensions)
    prefixes = sorted(
        {_slug(value) for value in normalized_dimensions.values() if _slug(value)},
        key=len,
        reverse=True,
    )
    for prefix in prefixes:
        marker = f"{prefix}_"
        if normalized_key.startswith(marker):
            normalized_key = normalized_key[len(marker) :]
            break
    return _METRIC_ALIASES.get(normalized_key, normalized_key)


def normalize_metric_period(period: str) -> str:
    """Normalize common quarter labels while preserving generic periods."""

    normalized = re.sub(r"[-_/]+", " ", period.strip().lower())
    normalized = re.sub(r"\s+", " ", normalized)
    match = re.fullmatch(r"(?:q([1-4])\s*(\d{4})|(\d{4})\s*q([1-4]))", normalized)
    if match:
        quarter = match.group(1) or match.group(4)
        year = match.group(2) or match.group(3)
        return f"Q{quarter} {year}"
    return normalized


def normalize_metric_unit(
    unit: str,
    comparison_type: MetricComparisonType,
) -> str:
    """Normalize units only when the numeric interpretation is unchanged."""

    normalized = _slug(unit)
    if (
        comparison_type is MetricComparisonType.RELATIVE_CHANGE
        and normalized in _RELATIVE_FRACTION_UNITS
    ):
        return "relative_change_fraction"
    return normalized


def normalize_metric_comparison(
    comparison: MetricComparison,
) -> MetricComparison:
    """Return the canonical application-boundary form of a comparison."""

    dimensions = normalize_metric_dimensions(comparison.dimensions)
    return comparison.model_copy(
        update={
            "metric_key": normalize_metric_key(comparison.metric_key, dimensions),
            "dimensions": dimensions,
            "baseline_period": normalize_metric_period(comparison.baseline_period),
            "comparison_period": normalize_metric_period(comparison.comparison_period),
            "unit": normalize_metric_unit(comparison.unit, comparison.comparison_type),
        }
    )


def metric_comparison_identity(
    comparison: MetricComparison,
) -> tuple[object, ...]:
    """Return the stable identity of a metric comparison, excluding its value."""

    comparison = normalize_metric_comparison(comparison)
    return (
        comparison.metric_key,
        tuple(
            sorted(
                (key, value.strip().lower())
                for key, value in comparison.dimensions.items()
            )
        ),
        comparison.baseline_period,
        comparison.comparison_period,
        comparison.comparison_type.value,
        comparison.unit,
    )


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
        deduplicated[identity] = comparison
    return list(deduplicated.values())


__all__ = [
    "MetricComparison",
    "MetricComparisonType",
    "MetricObservation",
    "deduplicate_metric_comparisons",
    "metric_comparison_identity",
    "normalize_metric_comparison",
    "normalize_metric_dimensions",
    "normalize_metric_key",
    "normalize_metric_period",
    "normalize_metric_unit",
]
