"""Shared resolution of executed evidence references."""

from __future__ import annotations

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
