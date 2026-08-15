"""Typed inputs and outputs for the Lead Data Scientist manager."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.findings import ConfidenceLevel, Finding
from schemas.run_state import Hypothesis, NonEmptyString


class SpecialistTask(BaseModel):
    """Bounded, typed objective supplied to a specialist-as-tool call."""

    model_config = ConfigDict(extra="forbid")

    objective: NonEmptyString
    data_scope: list[NonEmptyString] = Field(default_factory=list)
    hypothesis_ids: list[NonEmptyString] = Field(default_factory=list)
    required_outputs: list[NonEmptyString] = Field(default_factory=list)
    constraints: list[NonEmptyString] = Field(default_factory=list)


class LeadRecommendation(BaseModel):
    """A proposed action with explicit evidence provenance."""

    model_config = ConfigDict(extra="forbid")

    id: NonEmptyString
    statement: NonEmptyString
    evidence_refs: list[NonEmptyString] = Field(min_length=1)
    confidence: ConfidenceLevel
    caveats: list[NonEmptyString] = Field(default_factory=list)


class LeadResult(BaseModel):
    """Structured candidate answer produced by the Lead manager."""

    model_config = ConfigDict(extra="forbid")

    objective: NonEmptyString
    answer: NonEmptyString
    findings: list[Finding] = Field(default_factory=list)
    recommendations: list[LeadRecommendation] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    open_questions: list[NonEmptyString] = Field(default_factory=list)
    artifacts: list[NonEmptyString] = Field(default_factory=list)
    follow_up_analysis: bool = False
    follow_up_rationale: NonEmptyString | None = None
    caveats: list[NonEmptyString] = Field(default_factory=list)

    @model_validator(mode="after")
    def follow_up_decision_has_rationale(self) -> "LeadResult":
        """Make an affirmative follow-up decision explain its value."""

        if self.follow_up_analysis and self.follow_up_rationale is None:
            raise ValueError(
                "follow_up_rationale is required when follow_up_analysis is true"
            )
        return self


__all__ = ["LeadRecommendation", "LeadResult", "SpecialistTask"]
