"""The one citation-resolution contract used everywhere provenance is judged.

Runtime validation, Critic validation, offline evaluation, and offline
rescoring all resolve citations through this module, so a claim cannot be
supported at one boundary and unsupported at another.

Two rules make resolution lossless:

* resolving a claim's citations returns the resolved and the unresolved
  references explicitly. Nothing is dropped, so a fabricated or failed
  reference cannot disappear from a claim on its way through the system;
* a material claim is supported only when *every* reference it cites
  resolves. An "any reference resolves" test would let one real query
  launder an invented citation sitting beside it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from orchestration.ledger import AnalysisLedger
from schemas.findings import Finding
from schemas.hypotheses import hypothesis_requires_evidence
from schemas.run_state import ToolEventStatus


class EvidenceProvenanceError(ValueError):
    """Base class for every semantic citation failure.

    A response that satisfies its output schema but cites evidence that does not
    resolve is one failure mode with one operational meaning, whichever agent
    produced it. Sharing a base class is what lets the orchestration boundary
    classify it by type instead of by matching words in an error message, and
    keeps a new provenance error from silently landing in ``other``.
    """


def _event_references(event: object) -> set[str]:
    """Return references emitted by one tool event, regardless of status."""

    references = {event.id}
    references.update(
        event.artifact_refs
        if isinstance(getattr(event, "artifact_refs", None), list)
        else ()
    )
    arguments = getattr(event, "arguments", {})
    references.update(
        value
        for key in ("query_id", "query_path", "script_id", "script_path")
        if isinstance(value := arguments.get(key), str)
    )
    output = getattr(event, "output", None)
    if isinstance(output, dict):
        generated_refs = output.get("generated_evidence_refs", [])
        if isinstance(generated_refs, list):
            references.update(
                value for value in generated_refs if isinstance(value, str)
            )
    return references


def successful_tool_events(ledger: AnalysisLedger) -> tuple[object, ...]:
    """Return only tool events that completed successfully."""

    return tuple(
        event
        for event in ledger.tool_events
        if event.status is ToolEventStatus.SUCCEEDED
    )


def evidence_events(
    ledger: AnalysisLedger,
    references: list[str],
) -> tuple[object, ...]:
    """Resolve evidence references to successful tool events only."""

    reference_set = set(references)
    reference_set.update(
        artifact.path for artifact in ledger.artifacts if artifact.id in reference_set
    )
    return tuple(
        event
        for event in successful_tool_events(ledger)
        if _event_references(event).intersection(reference_set)
    )


def _artifact_is_verified(ledger: AnalysisLedger, artifact: object) -> bool:
    """Verify a persisted artifact without importing the artifact manager."""

    root = ledger.state_path.parent.parent.resolve()
    relative_path = getattr(artifact, "path", "")
    path_parts = Path(relative_path).parts if relative_path else ()
    if not path_parts or path_parts[0] not in {"working", "outputs"}:
        return False
    raw_candidate = root / relative_path
    try:
        raw_candidate.relative_to(root)
    except ValueError:
        return False
    current = root
    for part in path_parts:
        current /= part
        if current.is_symlink():
            return False
    try:
        candidate = raw_candidate.resolve(strict=True)
        candidate.relative_to(root)
        candidate.relative_to(root / path_parts[0])
    except (IndexError, OSError, ValueError):
        return False
    if not candidate.is_file():
        return False
    digest = sha256()
    size_bytes = 0
    try:
        with candidate.open("rb") as artifact_file:
            while chunk := artifact_file.read(1024 * 1024):
                digest.update(chunk)
                size_bytes += len(chunk)
    except OSError:
        return False
    return digest.hexdigest() == artifact.sha256 and size_bytes == artifact.size_bytes


def executed_references(ledger: AnalysisLedger) -> set[str]:
    """Return references backed by successful tools or verified artifacts.

    Failed events never contribute event IDs, paths, arguments, or generated
    evidence. A registered artifact may establish evidence only when its file
    still verifies and it is not exclusively associated with failed events.
    """

    successful_events = successful_tool_events(ledger)
    failed_events = tuple(
        event
        for event in ledger.tool_events
        if event.status is not ToolEventStatus.SUCCEEDED
    )
    successful_refs = set().union(
        *(_event_references(event) for event in successful_events)
    )
    failed_refs = set().union(*(_event_references(event) for event in failed_events))
    references = set(successful_refs)
    for artifact in ledger.artifacts:
        artifact_refs = {artifact.id, artifact.path}
        if artifact_refs.intersection(failed_refs) and not artifact_refs.intersection(
            successful_refs
        ):
            continue
        if _artifact_is_verified(ledger, artifact):
            references.update(artifact_refs)
    return references


def finding_reference_aliases(ledger: AnalysisLedger) -> dict[str, list[Finding]]:
    """Index canonical and local persisted finding IDs for unambiguous lookup."""

    aliases: dict[str, list[Finding]] = {}
    for finding in ledger.findings:
        aliases.setdefault(finding.id, []).append(finding)
        if ":" in finding.id:
            local_id = finding.id.rsplit(":", maxsplit=1)[1]
            aliases.setdefault(local_id, []).append(finding)
    return aliases


def resolve_evidence_reference(
    reference: str,
    *,
    executed_refs: set[str],
    aliases: dict[str, list[Finding]],
    resolving: set[str] | None = None,
) -> list[str]:
    """Resolve a direct reference or a uniquely identified finding reference."""

    if reference in executed_refs:
        return [reference]
    candidates = aliases.get(reference, [])
    if len(candidates) != 1:
        return []
    resolving = set() if resolving is None else resolving
    if reference in resolving:
        return []
    resolving.add(reference)
    resolved: list[str] = []
    for nested_reference in candidates[0].evidence_refs:
        resolved.extend(
            resolve_evidence_reference(
                nested_reference,
                executed_refs=executed_refs,
                aliases=aliases,
                resolving=resolving,
            )
        )
    return list(dict.fromkeys(resolved))


@dataclass(frozen=True, slots=True)
class CitationResolution:
    """The complete outcome of resolving one claim's citations.

    ``unresolved`` is the reason this type exists. Returning only the resolved
    references would silently discard a failed, fabricated, or ambiguous
    citation whenever a valid one happened to sit beside it.
    """

    references: tuple[str, ...]
    resolved: tuple[str, ...]
    unresolved: tuple[str, ...]

    @property
    def is_supported(self) -> bool:
        """Whether every cited reference resolved to executed evidence."""

        return bool(self.references) and not self.unresolved

    @property
    def canonical_references(self) -> tuple[str, ...]:
        """Canonical references with nothing dropped.

        Resolved citations are replaced by the exact executed references they
        stand for; unresolved ones are preserved verbatim so the claim still
        shows what it tried to cite.
        """

        return tuple(dict.fromkeys((*self.resolved, *self.unresolved)))


@dataclass(frozen=True, slots=True)
class ClaimCitations:
    """One material claim and the outcome of resolving its citations."""

    claim_id: str
    resolution: CitationResolution

    @property
    def supported(self) -> bool:
        return self.resolution.is_supported


def resolve_citations(
    references: Sequence[str],
    *,
    executed_refs: set[str],
    aliases: dict[str, list[Finding]],
) -> CitationResolution:
    """Resolve one claim's citations, reporting what did and did not resolve."""

    cited = tuple(dict.fromkeys(references))
    resolved: list[str] = []
    unresolved: list[str] = []
    for reference in cited:
        canonical = resolve_evidence_reference(
            reference,
            executed_refs=executed_refs,
            aliases=aliases,
        )
        if canonical:
            resolved.extend(canonical)
        else:
            unresolved.append(reference)
    return CitationResolution(
        references=cited,
        resolved=tuple(dict.fromkeys(resolved)),
        unresolved=tuple(unresolved),
    )


def _claim(claim_id: str, references: Iterable[str]) -> tuple[str, tuple[str, ...]]:
    return claim_id, tuple(references)


def material_claims(
    *,
    findings: Iterable[object] = (),
    recommendations: Iterable[object] = (),
    hypotheses: Iterable[object] = (),
    metric_comparisons: Iterable[object] = (),
    statistical_assessments: Iterable[object] = (),
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Enumerate every claim whose citations must resolve, with stable IDs.

    One definition of "material", shared by runtime validation and offline
    evaluation, so the two boundaries cannot disagree about which claims are
    held to the provenance contract. An open hypothesis is deliberately absent:
    it is still being tested and is allowed to carry no evidence at all.
    """

    claims: list[tuple[str, tuple[str, ...]]] = []
    claims.extend(_claim(f"finding:{item.id}", item.evidence_refs) for item in findings)
    claims.extend(
        _claim(f"recommendation:{item.id}", item.evidence_refs)
        for item in recommendations
    )
    claims.extend(
        _claim(f"hypothesis:{item.id}", item.evidence_refs)
        for item in hypotheses
        if hypothesis_requires_evidence(item.status)
    )
    claims.extend(
        _claim(f"metric_comparison:{item.metric_key}", item.evidence_refs)
        for item in metric_comparisons
    )
    claims.extend(
        _claim(f"statistical_assessment:{item.metric_key}", item.evidence_refs)
        for item in statistical_assessments
    )
    return tuple(claims)


def resolve_material_claims(
    claims: Iterable[tuple[str, tuple[str, ...]]],
    ledger: AnalysisLedger,
) -> tuple[ClaimCitations, ...]:
    """Resolve every material claim against one snapshot of executed evidence."""

    executed_refs = executed_references(ledger)
    aliases = finding_reference_aliases(ledger)
    return tuple(
        ClaimCitations(
            claim_id=claim_id,
            resolution=resolve_citations(
                references,
                executed_refs=executed_refs,
                aliases=aliases,
            ),
        )
        for claim_id, references in claims
    )


def unsupported_claim_ids(
    claims: Iterable[ClaimCitations],
) -> tuple[str, ...]:
    """Return the IDs of claims that cite anything which does not resolve."""

    return tuple(item.claim_id for item in claims if not item.supported)


def _approved_relation_names(ledger: AnalysisLedger) -> set[str]:
    """Derive safe relation names from the approved input filenames."""

    inputs = ledger.state_path.parent.parent / "inputs"
    names: set[str] = set()
    for path in inputs.rglob("*.parquet"):
        name = re.sub(r"[^A-Za-z0-9_]+", "_", path.stem).strip("_").lower()
        if name and name[0].isdigit():
            name = "_" + name
        if name:
            names.add(name)
    return names


def _evidence_event_source_path(ledger: AnalysisLedger, event: object) -> Path | None:
    """Resolve an event's saved query path without permitting workspace escape."""

    root = ledger.state_path.parent.parent.resolve()
    arguments = getattr(event, "arguments", {})
    references = [
        value
        for key in ("query_path", "script_path")
        if isinstance(value := arguments.get(key), str)
    ]
    references.extend(
        value for value in getattr(event, "artifact_refs", []) if isinstance(value, str)
    )
    for reference in references:
        candidate = (root / reference).resolve()
        try:
            candidate.relative_to(root / "working")
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def _sql_has_approved_source(sql: str, relation_names: set[str]) -> bool:
    """Return whether SQL visibly reads an approved input relation."""

    without_comments = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.DOTALL)
    cte_names = {
        match.group(1).lower()
        for match in re.finditer(
            r"(?:\bwith\b|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(",
            without_comments,
            re.IGNORECASE,
        )
    }
    return any(
        name.lower() not in cte_names
        and re.search(
            rf"\b(?:from|join|update|into|using)\s+"
            rf"['\"]?{re.escape(name)}['\"]?(?=\s|[,;)]|$)",
            without_comments,
            re.IGNORECASE,
        )
        for name in relation_names
    )


def has_source_lineage(ledger: AnalysisLedger, references: list[str]) -> bool:
    """Reject material claims whose only inspectable SQL is hard-coded data.

    Legacy test doubles may record an event without its source file; those remain
    accepted for compatibility. When an executed SQL artifact is present, it
    must visibly read an approved input relation.
    """

    referenced_artifacts = [
        artifact
        for artifact in ledger.artifacts
        if artifact.id in references or artifact.path in references
    ]
    event_references = set(references)
    event_references.update(artifact.path for artifact in referenced_artifacts)
    known_event_references = set().union(
        *(_event_references(event) for event in ledger.tool_events)
    )
    events = [
        event
        for event in successful_tool_events(ledger)
        if _event_references(event).intersection(event_references)
    ]
    has_known_reference = event_references.intersection(known_event_references)
    if (referenced_artifacts or has_known_reference) and not events:
        # A checksum proves file integrity, not that the file was produced by
        # a successful approved execution. Require a successful tool-event
        # relationship before an artifact or failed event can be provenance.
        return False
    if not referenced_artifacts and not has_known_reference:
        return False
    inspected_sql = False
    inspected_python = False
    relation_names = _approved_relation_names(ledger)
    for event in events:
        if getattr(event, "tool_name", None) != "run_sql":
            continue
        source_path = _evidence_event_source_path(ledger, event)
        if source_path is None or source_path.suffix.lower() != ".sql":
            continue
        inspected_sql = True
        try:
            source_text = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if _sql_has_approved_source(source_text, relation_names):
            return True
    for event in events:
        if getattr(event, "tool_name", None) != "run_python":
            continue
        source_path = _evidence_event_source_path(ledger, event)
        if source_path is None or not relation_names:
            continue
        inspected_python = True
        try:
            source_text = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if any(
            token in source_text
            for token in ("/workspace/inputs", "inputs/", *relation_names)
        ):
            return True
    return not inspected_sql and not inspected_python
