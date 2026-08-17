"""Shared resolution of executed evidence references."""

from __future__ import annotations

import re
from pathlib import Path

from orchestration.ledger import AnalysisLedger
from schemas.findings import Finding


def executed_references(ledger: AnalysisLedger) -> set[str]:
    """Return exact references emitted by approved tools or saved artifacts."""

    references = {event.id for event in ledger.tool_events}
    for event in ledger.tool_events:
        references.update(event.artifact_refs)
        references.update(
            value
            for key in ("query_id", "query_path", "script_id", "script_path")
            if isinstance(value := event.arguments.get(key), str)
        )
        if event.output is not None:
            generated_refs = event.output.get("generated_evidence_refs", [])
            if isinstance(generated_refs, list):
                references.update(
                    value for value in generated_refs if isinstance(value, str)
                )
    references.update(artifact.id for artifact in ledger.artifacts)
    references.update(artifact.path for artifact in ledger.artifacts)
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


def canonicalize_evidence_refs(
    references: list[str],
    *,
    executed_refs: set[str],
    aliases: dict[str, list[Finding]],
) -> list[str]:
    """Replace intermediate finding IDs with exact executed evidence refs."""

    resolved: list[str] = []
    for reference in references:
        resolved.extend(
            resolve_evidence_reference(
                reference,
                executed_refs=executed_refs,
                aliases=aliases,
            )
        )
    return list(dict.fromkeys(resolved))


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
    return any(
        re.search(
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
    events = [
        event
        for event in ledger.tool_events
        if any(
            reference
            in {
                event.id,
                *event.artifact_refs,
                *(
                    value
                    for value in event.arguments.values()
                    if isinstance(value, str)
                ),
            }
            for reference in event_references
        )
    ]
    if referenced_artifacts and not events:
        # A checksum proves file integrity, not that the file was produced by
        # an approved execution. Require a tool-event relationship before an
        # artifact can be the sole provenance of a material claim.
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
