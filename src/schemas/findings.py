"""Schemas for specialist findings and structured specialist results."""

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from schemas.common import NonEmptyString

if TYPE_CHECKING:
    from schemas.metrics import MetricComparison
    from schemas.statistics import StatisticalAssessment


class ConfidenceLevel(StrEnum):
    """Confidence attached to an analytical finding."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Finding(BaseModel):
    """A concise claim supported by traceable analytical evidence."""

    model_config = ConfigDict(extra="forbid")

    id: NonEmptyString
    statement: NonEmptyString
    metric: NonEmptyString | None = None
    value: float | None = None
    value_unit: NonEmptyString | None = None
    evidence_refs: list[NonEmptyString] = Field(min_length=1)
    confidence: ConfidenceLevel
    caveats: list[NonEmptyString] = Field(default_factory=list)


class SpecialistResult(BaseModel):
    """Structured output returned by a specialist to the Lead agent."""

    model_config = ConfigDict(extra="forbid")

    objective: NonEmptyString
    findings: list[Finding] = Field(default_factory=list)
    metric_comparisons: list["MetricComparison"] = Field(default_factory=list)
    statistical_assessments: list["StatisticalAssessment"] = Field(default_factory=list)
    artifacts: list[NonEmptyString] = Field(default_factory=list)
    methods_used: list[NonEmptyString] = Field(default_factory=list)
    follow_up_questions: list[NonEmptyString] = Field(default_factory=list)
    caveats: list[NonEmptyString] = Field(default_factory=list)


# ``schemas.metrics`` uses the shared primitive rather than importing this
# module, so resolving the typed forward reference here does not create a
# circular import.
from schemas.metrics import MetricComparison  # noqa: E402
from schemas.statistics import StatisticalAssessment  # noqa: E402

SpecialistResult.model_rebuild(
    _types_namespace={
        "MetricComparison": MetricComparison,
        "StatisticalAssessment": StatisticalAssessment,
    }
)


def canonicalize_specialist_result(
    result: SpecialistResult,
    namespace: str,
) -> SpecialistResult:
    """Namespace specialist-local finding IDs for persistent run state.

    Models can safely use concise local identifiers such as ``F1``. The
    application owns global identity, so the namespace is applied
    deterministically at the persistence boundary and repeated application is
    idempotent for the same specialist.
    """

    normalized_namespace = namespace.strip().lower()
    if not normalized_namespace:
        raise ValueError("specialist finding namespace must be non-empty")

    prefix = f"{normalized_namespace}:"
    findings: list[Finding] = []
    seen_ids: set[str] = set()
    for finding in result.findings:
        canonical_id = (
            finding.id if finding.id.startswith(prefix) else prefix + finding.id
        )
        if canonical_id in seen_ids:
            raise ValueError(
                f"duplicate finding id in {normalized_namespace} result: {canonical_id}"
            )
        seen_ids.add(canonical_id)
        findings.append(finding.model_copy(update={"id": canonical_id}))

    return result.model_copy(update={"findings": findings})
