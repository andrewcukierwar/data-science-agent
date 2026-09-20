"""Explicit compatibility setup for pre-binding, model-authored test records.

These fixtures exercise earlier lifecycle/provenance contracts. They do not
claim numerical binding; P0.2 coverage uses real version-1.2 ledgers instead.
No existing workspace or persisted benchmark evidence is rewritten.
"""

from orchestration.ledger import AnalysisLedger


def legacy_ledger(workspace_or_state_path, **kwargs) -> AnalysisLedger:
    new = not AnalysisLedger._resolve_state_path(workspace_or_state_path).exists()
    ledger = AnalysisLedger(workspace_or_state_path, **kwargs)
    if new:
        mark_legacy_fixture(ledger)
    return ledger


def mark_legacy_fixture(ledger: AnalysisLedger) -> None:
    """Mark a newly generated fake-agent fixture as the contract it supplies."""
    ledger.state.schema_version = "1.1"
    ledger.save()
