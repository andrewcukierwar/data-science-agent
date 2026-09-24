"""Typed validation, objection-evidence, and finalization repair contracts."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    model_validator,
)

from schemas.findings import Finding
from schemas.hypotheses import Hypothesis
from schemas.json_evidence import (
    JsonEvidenceNode,
    decode_json_evidence,
    encode_json_evidence,
)
from schemas.metrics import MetricComparison, MetricConflict
from schemas.statistics import StatisticalAssessment

NonEmptyString = Annotated[str, Field(min_length=1)]
VALIDATION_CONTRACT_VERSION = "1.0"


class ValidationStatus(StrEnum):
    PASS = "pass"
    REVISE = "revise"


class ValidationSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ValidationIssue(BaseModel):
    """Legacy issue shape retained for old evidence and deterministic adapters."""

    model_config = ConfigDict(extra="forbid")
    id: NonEmptyString
    severity: ValidationSeverity
    message: NonEmptyString
    category: NonEmptyString | None = None
    evidence_refs: list[NonEmptyString] = Field(default_factory=list)
    recommendation: NonEmptyString | None = None


class BlockerCategory(StrEnum):
    WRONG_GRAIN = "wrong_grain"
    WRONG_DENOMINATOR = "wrong_denominator"
    INCORRECT_NUMERICAL_CLAIM = "incorrect_numerical_claim"
    UNSUPPORTED_ASSERTED_FACT = "unsupported_asserted_fact"
    MISSING_REQUESTED_COMPARISON = "missing_requested_comparison"
    SELECTED_RESULT_CONFLICT = "selected_result_conflict"
    OBJECTIVE_NOT_ANSWERED = "objective_not_answered"


class RepairClass(StrEnum):
    SYNTHESIS_SELECTION = "synthesis_selection"
    COMPUTATION = "computation"
    IMPOSSIBLE_WITH_CURRENT_DATA = "impossible_with_current_data"


class LimitationCategory(StrEnum):
    EXTERNAL_VALIDITY = "external_validity"
    UNAVAILABLE_CAUSAL_MECHANISM = "unavailable_causal_mechanism"
    OPTIONAL_SEGMENTATION = "optional_segmentation"
    FUTURE_VALIDATION = "future_validation"
    UNAVAILABLE_NONESSENTIAL_INFORMATION = "unavailable_nonessential_information"


class EvidenceAnchorSource(StrEnum):
    CANDIDATE = "candidate"
    TOOL_EVENT = "tool_event"


class ObjectionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: EvidenceAnchorSource
    pointer: NonEmptyString
    # Present in provider output so a native retained object that resembles a
    # tagged node remains unambiguous when an old workspace is loaded.
    wire_format: Literal["typed_json"] = Field(default="typed_json", exclude=True)
    value: JsonEvidenceNode
    event_id: NonEmptyString | None = None

    @model_validator(mode="before")
    @classmethod
    def encode_value(cls, data: object) -> object:
        if isinstance(data, dict) and "value" in data and "wire_format" not in data:
            return {**data, "value": encode_json_evidence(data["value"])}
        return data

    @field_serializer("value")
    def serialize_value(self, value: JsonEvidenceNode) -> object:
        return decode_json_evidence(value)

    @model_validator(mode="after")
    def event_identity_matches_source(self) -> "ObjectionEvidence":
        if self.source is EvidenceAnchorSource.TOOL_EVENT and self.event_id is None:
            raise ValueError("tool-event evidence requires event_id")
        if self.source is EvidenceAnchorSource.CANDIDATE and self.event_id is not None:
            raise ValueError("candidate evidence cannot contain event_id")
        return self


class ValidationBlocker(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: NonEmptyString | None = None
    category: BlockerCategory
    requirement_id: NonEmptyString
    target_id: NonEmptyString
    affected_result_ids: list[NonEmptyString] = Field(default_factory=list)
    objective_clause: NonEmptyString | None = None
    evidence: list[ObjectionEvidence] = Field(min_length=1)
    message: NonEmptyString
    smallest_feasible_repair: NonEmptyString
    repair_class: RepairClass


class ValidationLimitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: LimitationCategory
    message: NonEmptyString
    evidence: list[ObjectionEvidence] = Field(default_factory=list)
    requirement_id: NonEmptyString | None = None
    target_id: NonEmptyString | None = None


class CatalogRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: NonEmptyString
    kind: NonEmptyString
    text: NonEmptyString


class CatalogTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: NonEmptyString
    kind: NonEmptyString
    candidate_pointer: NonEmptyString | None = None
    result_ids: tuple[NonEmptyString, ...] = ()


class ValidationCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: Literal[VALIDATION_CONTRACT_VERSION] = VALIDATION_CONTRACT_VERSION
    objective_identity: NonEmptyString
    requirements: tuple[CatalogRequirement, ...]
    targets: tuple[CatalogTarget, ...]


class CriticCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objective: NonEmptyString
    answer: NonEmptyString
    findings: list[Finding] = Field(default_factory=list)
    metric_comparisons: list[MetricComparison] = Field(default_factory=list)
    statistical_assessments: list[StatisticalAssessment] = Field(default_factory=list)
    caveats: list[NonEmptyString] = Field(default_factory=list)
    metric_conflicts: list[MetricConflict] = Field(default_factory=list)
    recommendations: list[NonEmptyString] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    open_questions: list[NonEmptyString] = Field(default_factory=list)
    follow_up_analysis: bool = False
    follow_up_rationale: NonEmptyString | None = None
    artifacts: list[NonEmptyString] = Field(default_factory=list)
    evidence_refs: list[NonEmptyString] = Field(default_factory=list)
    structured_metrics_required: bool = False
    visualization_requested: bool = False

    @model_validator(mode="after")
    def follow_up_decision_has_rationale(self) -> "CriticCandidate":
        if self.follow_up_analysis and self.follow_up_rationale is None:
            raise ValueError(
                "follow_up_rationale is required when follow_up_analysis is true"
            )
        return self


class ValidationResult(BaseModel):
    """Versioned review result; null version/typed fields denote legacy evidence."""

    model_config = ConfigDict(extra="forbid")
    contract_version: Literal[VALIDATION_CONTRACT_VERSION] | None = None
    status: ValidationStatus
    blockers: list[ValidationBlocker] = Field(default_factory=list)
    limitations: list[ValidationLimitation] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    checked_finding_ids: list[NonEmptyString] = Field(default_factory=list)
    summary: NonEmptyString | None = None
    remediation_cycles: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def revision_contract_is_consistent(self) -> "ValidationResult":
        has_high = any(
            issue.severity is ValidationSeverity.HIGH for issue in self.issues
        )
        if self.status is ValidationStatus.PASS and (self.blockers or has_high):
            raise ValueError("blocking validation defects require status 'revise'")
        return self


class RepairStatus(StrEnum):
    ATTEMPTED = "attempted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CONSTRAINED = "constrained"


class FinalizationRepairRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt_id: NonEmptyString
    blocker_ids: list[NonEmptyString]
    status: RepairStatus
    prior_selected_result_ids: list[NonEmptyString] = Field(default_factory=list)
    repaired_selected_result_ids: list[NonEmptyString] = Field(default_factory=list)
    stop_reason: NonEmptyString | None = None


__all__ = [name for name in globals() if not name.startswith("_")]
