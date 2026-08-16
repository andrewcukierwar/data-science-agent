"""Generic typed metric observations used across business-analysis tasks."""

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


def metric_comparison_identity(
    comparison: MetricComparison,
) -> tuple[object, ...]:
    """Return the stable identity of a metric comparison, excluding its value."""

    return (
        comparison.metric_key.strip().lower(),
        tuple(
            sorted(
                (key.strip().lower(), value.strip().lower())
                for key, value in comparison.dimensions.items()
            )
        ),
        comparison.baseline_period.strip().lower(),
        comparison.comparison_period.strip().lower(),
        comparison.comparison_type.value,
        comparison.unit.strip().lower(),
    )


def deduplicate_metric_comparisons(
    comparisons: list[MetricComparison],
) -> list[MetricComparison]:
    """Keep the first comparison for each deterministic generic identity."""

    deduplicated: list[MetricComparison] = []
    seen: set[tuple[object, ...]] = set()
    for comparison in comparisons:
        identity = metric_comparison_identity(comparison)
        if identity in seen:
            continue
        seen.add(identity)
        deduplicated.append(comparison)
    return deduplicated


__all__ = [
    "MetricComparison",
    "MetricComparisonType",
    "MetricObservation",
    "deduplicate_metric_comparisons",
    "metric_comparison_identity",
]
