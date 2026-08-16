"""Schemas for Critic validation results and remediation issues."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.findings import Finding
from schemas.hypotheses import Hypothesis

NonEmptyString = Annotated[str, Field(min_length=1)]


class ValidationStatus(StrEnum):
    """Whether candidate conclusions passed Critic review."""

    PASS = "pass"
    REVISE = "revise"


class ValidationSeverity(StrEnum):
    """Severity of a validation issue."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ValidationIssue(BaseModel):
    """A concrete problem found while checking an analysis."""

    model_config = ConfigDict(extra="forbid")

    id: NonEmptyString
    severity: ValidationSeverity
    message: NonEmptyString
    category: NonEmptyString | None = None
    evidence_refs: list[NonEmptyString] = Field(default_factory=list)
    recommendation: NonEmptyString | None = None


class CriticCandidate(BaseModel):
    """Candidate analysis and evidence supplied to the Critic."""

    model_config = ConfigDict(extra="forbid")

    objective: NonEmptyString
    answer: NonEmptyString
    findings: list[Finding] = Field(default_factory=list)
    recommendations: list[NonEmptyString] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    open_questions: list[NonEmptyString] = Field(default_factory=list)
    follow_up_analysis: bool = False
    follow_up_rationale: NonEmptyString | None = None
    artifacts: list[NonEmptyString] = Field(default_factory=list)
    evidence_refs: list[NonEmptyString] = Field(default_factory=list)

    @model_validator(mode="after")
    def follow_up_decision_has_rationale(self) -> "CriticCandidate":
        """Keep the Lead's follow-up decision explicit for Critic review."""

        if self.follow_up_analysis and self.follow_up_rationale is None:
            raise ValueError(
                "follow_up_rationale is required when follow_up_analysis is true"
            )
        return self


class ValidationResult(BaseModel):
    """Structured PASS/REVISE result returned by the Critic."""

    model_config = ConfigDict(extra="forbid")

    status: ValidationStatus
    issues: list[ValidationIssue] = Field(default_factory=list)
    checked_finding_ids: list[NonEmptyString] = Field(default_factory=list)
    summary: NonEmptyString | None = None
    remediation_cycles: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def high_severity_issue_requires_revision(self) -> "ValidationResult":
        has_high_severity_issue = any(
            issue.severity is ValidationSeverity.HIGH for issue in self.issues
        )
        if self.status is ValidationStatus.PASS and has_high_severity_issue:
            raise ValueError(
                "a high-severity issue requires validation status 'revise'"
            )
        return self
