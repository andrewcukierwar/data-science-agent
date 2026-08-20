"""One bounded correction attempt for a semantic provenance failure.

A response that satisfies the strict output schema but cites evidence that does
not resolve is not a malformed model response — it is a valid document making an
unsupported claim. The 2026-08-20 multi-agent canary failed exactly that way:
the model returned well-formed JSON in which one hypothesis cited
``completed_data_audit``, a reference that never existed.

Terminating there wastes an entire run over a citation the model could have
fixed if it had been told which field was wrong and which references were
available. Retrying the whole run instead would be opportunistic resampling
until a favourable output appears, which is exactly what the provenance gate
exists to prevent.

This module takes the narrow path between those: **one** configured correction
attempt, driven by a correction agent with no tools at all, given the invalid
field IDs and a bounded catalog of references that already exist. It cannot
run SQL, run Python, invoke a specialist, or start a Critic loop, so it reuses
the run's existing executions and spends no additional resource budget. A
second invalid response terminates with the provenance failure; nothing is
rewritten by the application and nothing is retried again.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agents import Agent
from agents.audit_evidence import AuditEvidenceCatalog
from agents.evidence import executed_references
from agents.model_usage import run_agent_with_usage
from agents.output_contract import require_strict_output, strict_output_type
from agents.runtime import (
    DEFAULT_EVIDENCE_CORRECTION_ATTEMPTS,
    MAX_EVIDENCE_CORRECTION_ATTEMPTS,
    AgentRunContext,
)
from orchestration.ledger import AnalysisLedger
from schemas.run_state import AgentEventStatus

EVIDENCE_CORRECTION_CONTRACT_VERSION = "1.0"

# Bounds for the model-visible catalog.
MAX_CATALOG_REFERENCES = 80
MAX_CATALOG_FINDINGS = 40
MAX_CATALOG_STATEMENT_CHARS = 240
CORRECTION_TURN_LIMIT = 1


class FindingEvidenceEntry(BaseModel):
    """One persisted specialist finding and the references behind it."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)


class EvidenceCorrectionCatalog(BaseModel):
    """Every reference the run can legitimately cite, bounded for one prompt.

    This is derived entirely from the run's own executed evidence. It contains
    no scenario ground truth, no evaluator rules, and no internal orchestration
    state — only what the agents already produced through approved tools.
    """

    model_config = ConfigDict(extra="forbid")

    catalog_version: Literal[EVIDENCE_CORRECTION_CONTRACT_VERSION] = (
        EVIDENCE_CORRECTION_CONTRACT_VERSION
    )
    executed_references: tuple[str, ...] = Field(default_factory=tuple)
    specialist_findings: tuple[FindingEvidenceEntry, ...] = Field(default_factory=tuple)
    audit_claims: AuditEvidenceCatalog | None = None
    truncated: bool = False


def build_evidence_correction_catalog(
    ledger: AnalysisLedger,
    *,
    audit_evidence: AuditEvidenceCatalog | None = None,
    reference_limit: int = MAX_CATALOG_REFERENCES,
    finding_limit: int = MAX_CATALOG_FINDINGS,
    statement_chars: int = MAX_CATALOG_STATEMENT_CHARS,
) -> EvidenceCorrectionCatalog:
    """Collect the citable references a correction attempt may choose from."""

    references = sorted(executed_references(ledger))
    bounded_references = tuple(references[:reference_limit])
    findings = [
        FindingEvidenceEntry(
            finding_id=finding.id,
            statement=finding.statement[:statement_chars],
            evidence_refs=tuple(finding.evidence_refs),
        )
        for finding in ledger.findings
    ]
    bounded_findings = tuple(findings[:finding_limit])
    return EvidenceCorrectionCatalog(
        executed_references=bounded_references,
        specialist_findings=bounded_findings,
        audit_claims=audit_evidence,
        truncated=(
            len(bounded_references) < len(references)
            or len(bounded_findings) < len(findings)
        ),
    )


EVIDENCE_CORRECTION_INSTRUCTIONS = """You are correcting evidence references in
a structured analytical result you already produced.

Your previous response satisfied the output schema but cited evidence that does
not resolve to a successful execution or a verified artifact. You are being
given the exact fields that failed and a catalog of every reference this run can
legitimately cite.

You have no tools. No SQL, no Python, no specialist, no further analysis. The
only information available is what is in this message.

Rules:

- Change only evidence_refs. Copy every other field of the previous output
  through unchanged: the answer text, statements, metric keys, dimensions,
  periods, units, numeric values, confidence, and caveats.
- Every reference you write must appear verbatim in
  executed_references, in a specialist finding's evidence_refs, or in an audit
  claim's evidence_refs from the catalog. Never invent a reference, never cite a
  failed execution, and never cite a claim_id, a finding ID, or a prose label.
- If a claim genuinely cannot be supported by any catalog reference, remove that
  claim, or return the hypothesis to open, and say so in the caveats. Removing
  an unsupported claim is correct; manufacturing a citation for it is not.
- Do not add new findings, metrics, recommendations, or hypotheses.

Return only the complete corrected typed result.
"""


def build_evidence_correction_agent(
    output_type: type[BaseModel],
    *,
    model: str | None,
    agent_name: str,
) -> Agent[AgentRunContext]:
    """Build a tool-less agent that can only re-emit a corrected result."""

    return Agent[AgentRunContext](
        name=agent_name,
        instructions=EVIDENCE_CORRECTION_INSTRUCTIONS,
        model=model,
        tools=[],
        handoffs=[],
        output_type=strict_output_type(output_type),
    )


def build_correction_prompt(
    previous_output: BaseModel,
    *,
    invalid_fields: tuple[str, ...],
    reason: str,
    catalog: EvidenceCorrectionCatalog,
) -> str:
    """Build the bounded correction request supplied to the model."""

    return "\n\n".join(
        (
            "EVIDENCE_CORRECTION_REQUEST",
            "INVALID_OUTPUT_FIELDS:\n"
            + (
                "\n".join(f"- {field}" for field in invalid_fields)
                if invalid_fields
                else "- (the validator did not name a specific field)"
            ),
            f"VALIDATION_ERROR:\n{reason}",
            "CITABLE_EVIDENCE_CATALOG_JSON:\n" + catalog.model_dump_json(indent=2),
            "PREVIOUS_OUTPUT_JSON:\n" + previous_output.model_dump_json(indent=2),
            (
                "Return the corrected result now. Change only evidence_refs; "
                "leave every claim, number, and statement exactly as it was, or "
                "remove a claim you cannot support."
            ),
        )
    )


async def run_bounded_evidence_correction[OutputT: BaseModel](
    context: AgentRunContext,
    previous_output: OutputT,
    error: ValueError,
    *,
    output_type: type[OutputT],
    persist: Callable[[OutputT], OutputT],
    agent_name: str,
    model: str | None,
    audit_evidence: AuditEvidenceCatalog | None = None,
) -> OutputT:
    """Spend one correction attempt, or re-raise the provenance failure.

    ``persist`` is the same validating persistence boundary that rejected the
    first response, so the correction is held to an identical standard. A
    corrected response is persisted only if it passes that boundary unchanged;
    the application never edits the model's citations on its way through.
    """

    attempts = _configured_attempts(context)
    invalid_fields = tuple(getattr(error, "invalid_fields", ()) or ())
    _record_event(
        context,
        agent_name=agent_name,
        status=AgentEventStatus.FAILED,
        model=model,
        output_type=output_type.__name__,
        error=str(error),
        started_at=datetime.now(UTC),
    )
    if attempts < 1:
        raise error

    correction_name = f"{agent_name} (evidence correction)"
    catalog = build_evidence_correction_catalog(
        context.ledger,
        audit_evidence=audit_evidence,
    )
    prompt = build_correction_prompt(
        previous_output,
        invalid_fields=invalid_fields,
        reason=str(error),
        catalog=catalog,
    )
    started_at = datetime.now(UTC)
    agent = build_evidence_correction_agent(
        output_type,
        model=model,
        agent_name=correction_name,
    )
    try:
        result = await run_agent_with_usage(
            agent,
            prompt,
            context=context,
            max_turns=CORRECTION_TURN_LIMIT,
        )
        corrected = require_strict_output(
            result.final_output,
            output_type,
            agent_name=correction_name,
        )
        persisted = persist(corrected)
    except Exception as correction_error:
        _record_event(
            context,
            agent_name=correction_name,
            status=AgentEventStatus.FAILED,
            model=model,
            output_type=output_type.__name__,
            error=str(correction_error),
            started_at=started_at,
        )
        # One attempt, then stop. Re-raising the second failure keeps the
        # terminal outcome an explicit provenance failure instead of an
        # indefinite resample.
        raise
    _record_event(
        context,
        agent_name=correction_name,
        status=AgentEventStatus.SUCCEEDED,
        model=model,
        output_type=output_type.__name__,
        error=None,
        started_at=started_at,
    )
    return persisted


def _configured_attempts(context: AgentRunContext) -> int:
    limit = getattr(
        context.run_config,
        "evidence_correction_attempts",
        DEFAULT_EVIDENCE_CORRECTION_ATTEMPTS,
    )
    return min(int(limit), MAX_EVIDENCE_CORRECTION_ATTEMPTS)


def _record_event(
    context: AgentRunContext,
    *,
    agent_name: str,
    status: AgentEventStatus,
    model: str | None,
    output_type: str,
    error: str | None,
    started_at: datetime,
) -> None:
    """Keep both model calls attributable to the active attempt."""

    context.ledger.record_agent_event(
        agent_name=agent_name,
        agent_role=context.agent_role.value,
        status=status,
        model=model,
        objective="Correct invalid evidence references in the candidate result.",
        output_type=output_type,
        error=error,
        started_at=started_at,
    )


__all__ = [
    "CORRECTION_TURN_LIMIT",
    "DEFAULT_EVIDENCE_CORRECTION_ATTEMPTS",
    "EVIDENCE_CORRECTION_CONTRACT_VERSION",
    "EVIDENCE_CORRECTION_INSTRUCTIONS",
    "MAX_EVIDENCE_CORRECTION_ATTEMPTS",
    "EvidenceCorrectionCatalog",
    "FindingEvidenceEntry",
    "build_correction_prompt",
    "build_evidence_correction_agent",
    "build_evidence_correction_catalog",
    "run_bounded_evidence_correction",
]
