"""Typed V1 statistical expectations and specialist assessments."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.common import NonEmptyString


class StatisticalConclusion(StrEnum):
    """Conclusion classes used by deterministic experiment evaluators."""

    SIGNIFICANT_AND_PRACTICAL = "significant_and_practical"
    NOT_STATISTICALLY_SIGNIFICANT = "not_statistically_significant"
    SIGNIFICANT_BUT_IMMATERIAL = "significant_but_immaterial"


class CausalInterpretation(StrEnum):
    """Whether an assessment appropriately limits causal language."""

    ASSOCIATION_ONLY = "association_only"
    CAUSAL_EFFECT_SUPPORTED = "causal_effect_supported"


class ConfidenceInterval(BaseModel):
    """Finite interval for an estimated effect."""

    model_config = ConfigDict(extra="forbid")

    lower: float = Field(allow_inf_nan=False)
    upper: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> "ConfidenceInterval":
        if self.upper < self.lower:
            raise ValueError("confidence interval upper bound must be >= lower bound")
        return self


class StatisticalExpectation(BaseModel):
    """Evaluator-only expected result for one basic statistical estimand."""

    model_config = ConfigDict(extra="forbid")

    metric_key: NonEmptyString
    dimensions: dict[NonEmptyString, NonEmptyString] = Field(default_factory=dict)
    baseline_period: NonEmptyString
    comparison_period: NonEmptyString
    expected_conclusion: StatisticalConclusion
    confidence_level: float = Field(default=0.95, gt=0, lt=1)
    expected_estimate: float = Field(allow_inf_nan=False)
    estimate_tolerance: float = Field(ge=0, allow_inf_nan=False)
    expected_confidence_interval: ConfidenceInterval
    confidence_interval_tolerance: float = Field(ge=0, allow_inf_nan=False)
    expected_p_value: float = Field(ge=0, le=1, allow_inf_nan=False)
    p_value_tolerance: float = Field(ge=0, le=1, allow_inf_nan=False)
    expected_effect_size: float = Field(allow_inf_nan=False)
    effect_size_tolerance: float = Field(ge=0, allow_inf_nan=False)
    practical_significance_threshold: float = Field(ge=0, allow_inf_nan=False)
    expected_practically_significant: bool
    required_assumptions: tuple[NonEmptyString, ...] = Field(min_length=1)
    expected_causal_interpretation: CausalInterpretation = (
        CausalInterpretation.CAUSAL_EFFECT_SUPPORTED
    )


class StatisticalAssessment(BaseModel):
    """Typed statistician output required for a configured expectation."""

    model_config = ConfigDict(extra="forbid")

    metric_key: NonEmptyString
    dimensions: dict[NonEmptyString, NonEmptyString] = Field(default_factory=dict)
    baseline_period: NonEmptyString
    comparison_period: NonEmptyString
    method: NonEmptyString
    unit_of_analysis: NonEmptyString
    conclusion: StatisticalConclusion
    confidence_level: float = Field(gt=0, lt=1)
    estimate: float = Field(allow_inf_nan=False)
    confidence_interval: ConfidenceInterval
    p_value: float = Field(ge=0, le=1, allow_inf_nan=False)
    effect_size: float = Field(allow_inf_nan=False)
    practical_significance_threshold: float = Field(ge=0, allow_inf_nan=False)
    practically_significant: bool
    assumptions_checked: tuple[NonEmptyString, ...] = Field(min_length=1)
    causal_interpretation: CausalInterpretation
    evidence_refs: list[NonEmptyString] = Field(min_length=1)

    @field_validator("assumptions_checked")
    @classmethod
    def assumptions_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("assumptions_checked must contain unique entries")
        return value


__all__ = [
    "CausalInterpretation",
    "ConfidenceInterval",
    "StatisticalAssessment",
    "StatisticalConclusion",
    "StatisticalExpectation",
]
