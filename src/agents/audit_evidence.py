"""Typed audit provenance shared by both architectures.

The Data Auditor executes its checks with the same deterministic tools every
other agent uses, but its typed result crosses an architecture boundary: in the
multi-agent lifecycle the Lead receives a persisted audit and has no SQL,
Python, or internal-state access with which to rediscover how a claim was
established. This module makes that provenance travel with the claim.

Two things happen here, over the claim projection ``schemas.audit`` owns:

* the persistence boundary canonicalizes each claim's references against the
  ledger and refuses to persist a completed audit whose material claims have
  missing, failed, ambiguous, or fabricated provenance;
* a bounded, typed catalog exposes the surviving canonical references so the
  Lead can cite exact executed evidence instead of a pseudo-reference such as
  ``completed_data_audit``.

The offline evaluator enforces the same boundary from persisted state alone;
see ``evaluation.primitives.resolve_audit_claims``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agents.evidence import (
    executed_references,
    finding_reference_aliases,
    resolve_citations,
)
from agents.runtime import AgentRunContext
from orchestration.ledger import AnalysisLedger
from schemas.audit import (
    AuditClaim,
    AuditClaimKind,
    AuditResult,
    AuditStatus,
    audit_claims,
)

AUDIT_EVIDENCE_CATALOG_VERSION = "1.0"

# Bounds for the model-visible catalog. They are generous enough for a complete
# audit of the canonical dataset and exist so a pathological audit cannot grow
# the Lead prompt without limit.
MAX_CATALOG_ENTRIES = 64
MAX_CATALOG_REFS_PER_ENTRY = 8
MAX_CATALOG_STATEMENT_CHARS = 400


class AuditEvidenceError(ValueError):
    """Raised when material audit claims lack canonical executed provenance."""


class AuditEvidenceEntry(BaseModel):
    """One audit claim exposed to the Lead with its canonical references."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    kind: AuditClaimKind
    statement: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    table_name: str | None = None
    issue_id: str | None = None
    evidence_refs_truncated: bool = False


class AuditEvidenceCatalog(BaseModel):
    """Bounded typed provenance catalog handed to the Lead.

    ``claim_id`` names an audit claim; it is not itself an evidence reference.
    Only the ``evidence_refs`` of an entry may be cited.
    """

    model_config = ConfigDict(extra="forbid")

    catalog_version: Literal[AUDIT_EVIDENCE_CATALOG_VERSION] = (
        AUDIT_EVIDENCE_CATALOG_VERSION
    )
    audit_status: AuditStatus
    entries: tuple[AuditEvidenceEntry, ...] = Field(default_factory=tuple)
    entry_limit: int = Field(default=MAX_CATALOG_ENTRIES, ge=1)
    truncated: bool = False
    citable_references: tuple[str, ...] = Field(default_factory=tuple)


def canonicalize_audit_result(
    audit: AuditResult,
    ledger: AnalysisLedger,
) -> tuple[AuditResult, tuple[AuditClaim, ...]]:
    """Return the audit with canonical references and its unsupported claims.

    Claims whose references resolve are rewritten in place with the exact
    executed references. Claims that resolve to nothing keep the references the
    model supplied so the failure stays inspectable, and are returned as the
    unsupported set.
    """

    executed_refs = executed_references(ledger)
    aliases = finding_reference_aliases(ledger)

    def resolve(references: list[str]) -> tuple[list[str], bool]:
        # One reference that resolves does not launder another that does not,
        # so a claim is supported only when every citation resolves.
        resolution = resolve_citations(
            references,
            executed_refs=executed_refs,
            aliases=aliases,
        )
        return list(resolution.canonical_references), resolution.is_supported

    unsupported: list[AuditClaim] = []
    claims_by_id = {claim.claim_id: claim for claim in audit_claims(audit)}

    tables = []
    for table_index, table in enumerate(audit.tables):
        table_refs, table_ok = resolve(table.evidence_refs)
        if not table_ok:
            unsupported.append(claims_by_id[f"audit:table:{table_index}"])
        warnings = []
        for warning_index, warning in enumerate(table.warnings):
            warning_refs, warning_ok = resolve(warning.evidence_refs)
            if not warning_ok:
                unsupported.append(
                    claims_by_id[f"audit:table:{table_index}:warning:{warning_index}"]
                )
            warnings.append(warning.model_copy(update={"evidence_refs": warning_refs}))
        tables.append(
            table.model_copy(update={"evidence_refs": table_refs, "warnings": warnings})
        )

    issues = []
    for issue_index, issue in enumerate(audit.issues):
        issue_refs, issue_ok = resolve(issue.evidence_refs)
        if not issue_ok:
            unsupported.append(claims_by_id[f"audit:issue:{issue_index}"])
        issues.append(issue.model_copy(update={"evidence_refs": issue_refs}))

    limitations = []
    for limitation_index, limitation in enumerate(audit.limitations):
        limitation_refs, limitation_ok = resolve(limitation.evidence_refs)
        if not limitation_ok:
            unsupported.append(claims_by_id[f"audit:limitation:{limitation_index}"])
        limitations.append(
            limitation.model_copy(update={"evidence_refs": limitation_refs})
        )

    canonical_audit = audit.model_copy(
        update={"tables": tables, "issues": issues, "limitations": limitations}
    )
    return canonical_audit, tuple(unsupported)


def validate_audit_provenance(
    audit: AuditResult,
    ledger: AnalysisLedger,
) -> AuditResult:
    """Require every material audit claim to cite exact executed evidence.

    A blocked audit is exempt: it aborts the run before any of its statements
    can influence a candidate answer, and the lifecycle reports it under its own
    blocked-audit condition rather than as a provenance failure.
    """

    if audit.status is AuditStatus.BLOCKED:
        return audit
    canonical_audit, unsupported = canonicalize_audit_result(audit, ledger)
    if unsupported:
        details = ", ".join(
            f"{claim.claim_id}"
            + (f"[{claim.issue_id}]" if claim.issue_id else "")
            + ("" if claim.evidence_refs else " (no evidence_refs)")
            for claim in unsupported
        )
        raise AuditEvidenceError("audit claims cite no executed evidence: " + details)
    return canonical_audit


def build_audit_evidence_catalog(
    audit: AuditResult,
    ledger: AnalysisLedger,
    *,
    entry_limit: int = MAX_CATALOG_ENTRIES,
    refs_per_entry: int = MAX_CATALOG_REFS_PER_ENTRY,
    statement_chars: int = MAX_CATALOG_STATEMENT_CHARS,
) -> AuditEvidenceCatalog:
    """Build the bounded typed provenance catalog supplied to the Lead.

    Only claims that resolve to successful execution or a verified artifact are
    listed, so nothing in the catalog can be cited into an unsupported answer.
    """

    canonical_audit, _ = canonicalize_audit_result(audit, ledger)
    executed_refs = executed_references(ledger)
    entries: list[AuditEvidenceEntry] = []
    truncated = False
    for claim in audit_claims(canonical_audit):
        resolved = tuple(
            reference for reference in claim.evidence_refs if reference in executed_refs
        )
        if not resolved:
            continue
        if len(entries) >= entry_limit:
            truncated = True
            break
        bounded_refs = resolved[:refs_per_entry]
        statement = claim.statement[:statement_chars]
        entries.append(
            AuditEvidenceEntry(
                claim_id=claim.claim_id,
                kind=claim.kind,
                statement=statement,
                evidence_refs=bounded_refs,
                table_name=claim.table_name,
                issue_id=claim.issue_id,
                evidence_refs_truncated=len(bounded_refs) < len(resolved),
            )
        )
    citable = tuple(
        dict.fromkeys(
            reference for entry in entries for reference in entry.evidence_refs
        )
    )
    return AuditEvidenceCatalog(
        audit_status=canonical_audit.status,
        entries=tuple(entries),
        entry_limit=entry_limit,
        truncated=truncated,
        citable_references=citable,
    )


def persist_audit_result(
    audit: AuditResult,
    context: AgentRunContext,
) -> AuditResult:
    """Validate audit provenance and persist the canonical audit.

    Both architectures persist through this one boundary, so a semantically
    equivalent single-agent and multi-agent audit exposes the same claim-level
    provenance downstream.
    """

    return context.ledger.record_audit(validate_audit_provenance(audit, context.ledger))


__all__ = [
    "AUDIT_EVIDENCE_CATALOG_VERSION",
    "MAX_CATALOG_ENTRIES",
    "MAX_CATALOG_REFS_PER_ENTRY",
    "AuditClaim",
    "AuditClaimKind",
    "AuditEvidenceCatalog",
    "AuditEvidenceEntry",
    "AuditEvidenceError",
    "audit_claims",
    "build_audit_evidence_catalog",
    "canonicalize_audit_result",
    "persist_audit_result",
    "validate_audit_provenance",
]
