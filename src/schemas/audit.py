"""Schemas for deterministic data-audit results.

Contract version ``2.0`` replaces the provenance-free warning and limitation
strings of version ``1.0`` with typed, evidence-bearing observations, and gives
every table profile its own evidence references. A material audit claim can
influence the candidate answer, so it must carry the same canonical provenance
the rest of the evidence contract requires. Version ``1.0`` payloads remain
loadable: the coercion below preserves their statements and leaves their
provenance explicitly empty rather than inventing references for them.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonEmptyString = Annotated[str, Field(min_length=1)]
Rate = Annotated[float, Field(ge=0.0, le=1.0)]

AUDIT_CONTRACT_VERSION = "2.0"
LEGACY_AUDIT_CONTRACT_VERSION = "1.0"
SUPPORTED_AUDIT_CONTRACT_VERSIONS = frozenset(
    {LEGACY_AUDIT_CONTRACT_VERSION, AUDIT_CONTRACT_VERSION}
)


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


class AuditObservation(BaseModel):
    """A material audit statement bound to executed evidence.

    The observation carries no identifier of its own. Claim identity is owned by
    the application, which derives deterministic, collision-free claim IDs from
    the observation's position in the audit.
    """

    model_config = ConfigDict(extra="forbid")

    statement: NonEmptyString
    evidence_refs: list[NonEmptyString] = Field(default_factory=list)


def _coerce_observations(value: Any) -> Any:
    """Accept contract 1.0 plain strings without fabricating provenance."""

    if not isinstance(value, list):
        return value
    return [
        {"statement": item, "evidence_refs": []} if isinstance(item, str) else item
        for item in value
    ]


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
    warnings: list[AuditObservation] = Field(default_factory=list)
    evidence_refs: list[NonEmptyString] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_warning_strings(cls, value: Any) -> Any:
        if isinstance(value, dict) and "warnings" in value:
            value = {**value, "warnings": _coerce_observations(value["warnings"])}
        return value


class AuditResult(BaseModel):
    """Structured output of the Data Auditor."""

    model_config = ConfigDict(extra="forbid")

    status: AuditStatus
    tables: list[TableAudit] = Field(default_factory=list)
    issues: list[DataQualityIssue] = Field(default_factory=list)
    limitations: list[AuditObservation] = Field(default_factory=list)
    audited_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_limitation_strings(cls, value: Any) -> Any:
        if isinstance(value, dict) and "limitations" in value:
            value = {**value, "limitations": _coerce_observations(value["limitations"])}
        return value

    @model_validator(mode="after")
    def audited_at_is_timezone_aware(self) -> "AuditResult":
        if self.audited_at.tzinfo is None or self.audited_at.utcoffset() is None:
            raise ValueError("audited_at must include timezone information")
        return self


class AuditClaimKind(StrEnum):
    """Where a material audit claim was stated."""

    TABLE_PROFILE = "table_profile"
    TABLE_WARNING = "table_warning"
    ISSUE = "issue"
    LIMITATION = "limitation"


@dataclass(frozen=True, slots=True)
class AuditClaim:
    """One material audit statement and the references the model supplied."""

    claim_id: str
    kind: AuditClaimKind
    statement: str
    evidence_refs: tuple[str, ...]
    table_name: str | None = None
    issue_id: str | None = None


def audit_claims(audit: AuditResult) -> tuple[AuditClaim, ...]:
    """Enumerate every material audit claim with a deterministic claim ID.

    This projection lives beside the contract rather than in the agent runtime
    so the offline evaluator can resolve the same claims, with the same IDs,
    without importing anything that executes agents.

    Claim IDs are positional so two claims can never collide, even when a model
    reuses an issue ID or repeats a table name.
    """

    claims: list[AuditClaim] = []
    for table_index, table in enumerate(audit.tables):
        claims.append(
            AuditClaim(
                claim_id=f"audit:table:{table_index}",
                kind=AuditClaimKind.TABLE_PROFILE,
                statement=(
                    f"{table.table_name}: {table.row_count} rows, duplicate rate "
                    f"{table.duplicate_rate}"
                ),
                evidence_refs=tuple(table.evidence_refs),
                table_name=table.table_name,
            )
        )
        for warning_index, warning in enumerate(table.warnings):
            claims.append(
                AuditClaim(
                    claim_id=f"audit:table:{table_index}:warning:{warning_index}",
                    kind=AuditClaimKind.TABLE_WARNING,
                    statement=warning.statement,
                    evidence_refs=tuple(warning.evidence_refs),
                    table_name=table.table_name,
                )
            )
    for issue_index, issue in enumerate(audit.issues):
        claims.append(
            AuditClaim(
                claim_id=f"audit:issue:{issue_index}",
                kind=AuditClaimKind.ISSUE,
                statement=f"[{issue.severity.value}] {issue.message}",
                evidence_refs=tuple(issue.evidence_refs),
                table_name=issue.table_name,
                issue_id=issue.id,
            )
        )
    for limitation_index, limitation in enumerate(audit.limitations):
        claims.append(
            AuditClaim(
                claim_id=f"audit:limitation:{limitation_index}",
                kind=AuditClaimKind.LIMITATION,
                statement=limitation.statement,
                evidence_refs=tuple(limitation.evidence_refs),
            )
        )
    return tuple(claims)


# Name used by some callers when the result is referred to as a data audit.
DataAuditResult = AuditResult
