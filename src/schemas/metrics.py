"""Generic typed metric observations used across business-analysis tasks."""

from enum import StrEnum
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas.findings import NonEmptyString


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


__all__ = ["MetricComparison", "MetricComparisonType", "MetricObservation"]
