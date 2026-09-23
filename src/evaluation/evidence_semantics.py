"""Select evidence semantics for an explicitly declared evaluator contract.

The strict runtime contract remains ``agents.evidence``. A frozen compatibility
implementation is available only to offline scoring of the historical
scenario 1.0 / evaluator 1.2 pair.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from agents import evidence as current_evidence
from agents.evidence import ClaimCitations
from evaluation import legacy_v12_evidence
from orchestration.ledger import AnalysisLedger

EvidenceClaims = Iterable[tuple[str, tuple[str, ...]]]


class EvidenceSemantics(Protocol):
    """Evidence operations consumed only by the offline evaluator."""

    def executed_references(self, ledger: AnalysisLedger) -> set[str]: ...

    def resolve_material_claims(
        self, claims: EvidenceClaims, ledger: AnalysisLedger
    ) -> tuple[ClaimCitations, ...]: ...

    def has_source_lineage(
        self, ledger: AnalysisLedger, references: list[str]
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class _EvidenceSemantics:
    executed_references: Callable[[AnalysisLedger], set[str]]
    resolve_material_claims: Callable[..., tuple[ClaimCitations, ...]]
    has_source_lineage: Callable[[AnalysisLedger, list[str]], bool]


CURRENT_EVIDENCE_SEMANTICS: EvidenceSemantics = _EvidenceSemantics(
    executed_references=current_evidence.executed_references,
    resolve_material_claims=current_evidence.resolve_material_claims,
    has_source_lineage=current_evidence.has_source_lineage,
)

LEGACY_V12_EVIDENCE_SEMANTICS: EvidenceSemantics = _EvidenceSemantics(
    executed_references=legacy_v12_evidence.executed_references,
    resolve_material_claims=legacy_v12_evidence.resolve_material_claims,
    has_source_lineage=legacy_v12_evidence.has_source_lineage,
)


def evidence_semantics_for_contract(
    *, scenario_version: str, evaluator_version: str
) -> EvidenceSemantics:
    """Select historical evidence only for the declared frozen v1.2 contract."""

    if scenario_version == "1.0" and evaluator_version == "1.2":
        return LEGACY_V12_EVIDENCE_SEMANTICS
    return CURRENT_EVIDENCE_SEMANTICS


__all__ = [
    "CURRENT_EVIDENCE_SEMANTICS",
    "LEGACY_V12_EVIDENCE_SEMANTICS",
    "EvidenceSemantics",
    "evidence_semantics_for_contract",
]
