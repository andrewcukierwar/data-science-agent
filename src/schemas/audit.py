"""Schemas for deterministic data-audit results."""

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonEmptyString = Annotated[str, Field(min_length=1)]
Rate = Annotated[float, Field(ge=0.0, le=1.0)]


class AuditStatus(StrEnum):
    """Overall state of a data audit."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    BLOCKED = "blocked"


class IssueSeverity(StrEnum):
    """Severity used for data-quality issues."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DateRange(BaseModel):
    """Inclusive date coverage for a table or dataset."""

    model_config = ConfigDict(extra="forbid")

    start: date
    end: date

    @model_validator(mode="after")
    def end_is_not_before_start(self) -> "DateRange":
        if self.end < self.start:
            raise ValueError("date range end must be on or after start")
        return self


class DataQualityIssue(BaseModel):
    """A specific, actionable problem identified during an audit."""

    model_config = ConfigDict(extra="forbid")

    id: NonEmptyString
    severity: IssueSeverity
    message: NonEmptyString
    table_name: NonEmptyString | None = None
    evidence_refs: list[NonEmptyString] = Field(default_factory=list)
    recommendation: NonEmptyString | None = None


class MissingnessObservation(BaseModel):
    """Missing-value rate for one named table column."""

    model_config = ConfigDict(extra="forbid")

    column: NonEmptyString
    rate: Rate


class TableAudit(BaseModel):
    """Observed schema and quality facts for one input table."""

    model_config = ConfigDict(extra="forbid")

    table_name: NonEmptyString
    row_count: int = Field(ge=0)
    date_range: DateRange | None = None
    duplicate_rate: Rate = 0.0
    missingness: list[MissingnessObservation] = Field(default_factory=list)
    primary_key_candidates: list[NonEmptyString] = Field(default_factory=list)
    relationships: list[NonEmptyString] = Field(default_factory=list)
    warnings: list[NonEmptyString] = Field(default_factory=list)


class AuditResult(BaseModel):
    """Structured output of the Data Auditor."""

    model_config = ConfigDict(extra="forbid")

    status: AuditStatus
    tables: list[TableAudit] = Field(default_factory=list)
    issues: list[DataQualityIssue] = Field(default_factory=list)
    limitations: list[NonEmptyString] = Field(default_factory=list)
    audited_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def audited_at_is_timezone_aware(self) -> "AuditResult":
        if self.audited_at.tzinfo is None or self.audited_at.utcoffset() is None:
            raise ValueError("audited_at must include timezone information")
        return self


# Name used by some callers when the result is referred to as a data audit.
DataAuditResult = AuditResult
