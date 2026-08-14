"""Schemas for Critic validation results and remediation issues."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
