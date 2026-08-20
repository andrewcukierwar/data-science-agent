"""R25 regressions: provenance failures are named, and the canary is retained.

The 2026-08-20 multi-agent canary failed because the Lead resolved a hypothesis
with the invented reference ``completed_data_audit``. Operationally that read as
``other`` — indistinguishable from a crash — and the only thing preventing a
repeat was a live provider call nobody could run deterministically.

These tests give that failure a name that survives every reporting boundary, and
reproduce the exact handoff offline so the regression cannot come back unnoticed.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from agents import (
    AnalystEvidenceError,
    AuditEvidenceError,
    LeadEvidenceError,
    StatisticianEvidenceError,
    persist_audit_result,
)
from agents.evidence import EvidenceProvenanceError
from agents.hypothesis_state import HypothesisEvidenceError
from agents.model_usage import Runner
from benchmark.runner import category_for_block_reason
from evaluation.contracts import ExecutionMode, FailureCategory
from orchestration.block_reasons import classify_exception, describe_reason
from orchestration.ledger import AnalysisLedger
from orchestration.runner import AnalysisRunner
from schemas.audit import AuditObservation, AuditResult, AuditStatus, TableAudit
from schemas.lead import LeadResult
from schemas.run_state import (
    AttemptStatus,
    Hypothesis,
    HypothesisStatus,
    RunBlockReason,
    RunStatus,
)
from schemas.validation import ValidationResult, ValidationStatus
from tools.workspace import WorkspaceManager

_STAMP = datetime(2026, 1, 1, tzinfo=UTC)
_FABRICATED = "completed_data_audit"


# --- every semantic citation failure shares one named reason -----------------


@pytest.mark.parametrize(
    "error_type",
    [
        LeadEvidenceError,
        AuditEvidenceError,
        HypothesisEvidenceError,
        AnalystEvidenceError,
        StatisticianEvidenceError,
    ],
)
def test_every_provenance_error_shares_the_semantic_base_class(
    error_type: type[Exception],
) -> None:
    """Classification is by type, never by matching words in a message."""

    assert issubclass(error_type, EvidenceProvenanceError)
    assert issubclass(error_type, ValueError)


def test_lead_evidence_failure_is_named_rather_than_other() -> None:
    error = LeadEvidenceError(
        "lead outputs cite no executed evidence: hypothesis:H2",
        ("hypothesis:H2",),
    )

    reason = classify_exception(error)

    assert reason is RunBlockReason.EVIDENCE_PROVENANCE
    assert reason is not RunBlockReason.OTHER
    assert reason is not RunBlockReason.SCHEMA_FAILURE
    assert "does not resolve" in describe_reason(reason)


def test_an_unknown_provenance_error_still_classifies_correctly() -> None:
    """A future provenance error inherits the taxonomy instead of `other`."""

    class FutureEvidenceError(EvidenceProvenanceError):
        pass

    assert (
        classify_exception(FutureEvidenceError("new boundary"))
        is RunBlockReason.EVIDENCE_PROVENANCE
    )


def test_the_named_reason_maps_to_its_own_benchmark_category() -> None:
    mapped = {reason: category_for_block_reason(reason) for reason in RunBlockReason}

    assert (
        mapped[RunBlockReason.EVIDENCE_PROVENANCE]
        is FailureCategory.EVIDENCE_PROVENANCE
    )
    assert set(mapped) == set(RunBlockReason)
    # A provenance failure is neither a malformed response nor a crash.
    for other in (
        FailureCategory.SCHEMA,
        FailureCategory.OTHER,
        FailureCategory.PROVIDER,
    ):
        assert mapped[RunBlockReason.EVIDENCE_PROVENANCE] is not other


# --- the 2026-08-20 canary, reproduced without a provider --------------------


def _canary_workspace(tmp_path: Path) -> Path:
    inputs_source = tmp_path / "canary-inputs"
    inputs_source.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"order_id": ["O1", "O2"], "net_revenue": [10.0, 12.0]}).to_parquet(
        inputs_source / "orders.parquet"
    )
    return inputs_source


def _evidence_bearing_audit(context) -> AuditResult:  # noqa: ANN001
    """A production-shaped audit: real checks, cited by their tool event."""

    inspection = context.sql_service.inspect_relations()
    assert inspection.tool_event_id is not None
    return AuditResult(
        status=AuditStatus.COMPLETE,
        tables=[
            TableAudit(
                table_name=relation.relation_name,
                row_count=relation.row_count or 0,
                evidence_refs=[inspection.tool_event_id],
            )
            for relation in inspection.relations
        ],
        limitations=[
            AuditObservation(
                statement="Only the registered input relations were inspected.",
                evidence_refs=[inspection.tool_event_id],
            )
        ],
        audited_at=_STAMP,
    )


def _run_canary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    lead_refs: list[list[str]],
):
    """Run the real multi-agent lifecycle with scripted Lead responses."""

    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        return persist_audit_result(_evidence_bearing_audit(context), context)

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        context.consume_budget("critic_loops")
        return ValidationResult(
            status=ValidationStatus.PASS,
            summary="The candidate cites executed evidence.",
        )

    queued = list(lead_refs)

    async def fake_run(agent, agent_input, *, context, **kwargs):  # noqa: ANN001
        refs = queued.pop(0)
        return SimpleNamespace(
            final_output=LeadResult(
                objective="Why did profitability decline?",
                answer="A reporting-day gap distorts the comparison.",
                hypotheses=[
                    Hypothesis(
                        id="H2",
                        statement="A data-quality gap distorts the comparison.",
                        status=HypothesisStatus.SUPPORTED,
                        evidence_refs=list(refs),
                    )
                ],
            )
        )

    monkeypatch.setattr(Runner, "run", fake_run)
    return asyncio.run(
        AnalysisRunner(
            workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
            auditor_runner=fake_auditor,
            critic_runner=fake_critic,
        ).run(
            "canary-regression",
            "Why did profitability decline?",
            inputs_source=_canary_workspace(tmp_path),
        )
    )


def test_the_2026_08_20_canary_failure_is_reproduced_and_named(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`completed_data_audit` twice: the run fails, explicitly, as provenance."""

    result = _run_canary(
        tmp_path,
        monkeypatch,
        lead_refs=[[_FABRICATED], [_FABRICATED]],
    )

    assert result.status is RunStatus.FAILED
    assert "hypothesis:H2" in result.error
    assert result.block_reason is RunBlockReason.EVIDENCE_PROVENANCE
    assert "does not resolve" in result.block_detail
    assert (
        category_for_block_reason(result.block_reason)
        is FailureCategory.EVIDENCE_PROVENANCE
    )


def test_the_canary_failure_propagates_into_attempt_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_canary(
        tmp_path,
        monkeypatch,
        lead_refs=[[_FABRICATED], [_FABRICATED]],
    )

    ledger = AnalysisLedger(result.workspace)
    attempt = ledger.attempt_history[-1]

    assert attempt.status is AttemptStatus.FAILED
    assert attempt.block_reason is RunBlockReason.EVIDENCE_PROVENANCE
    assert ledger.state.block_reason is RunBlockReason.EVIDENCE_PROVENANCE


def test_a_completed_attempt_carries_no_block_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only non-completions are classified; success is not a failure category."""

    audit_reference: dict[str, str] = {}

    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        audit = _evidence_bearing_audit(context)
        audit_reference["ref"] = audit.tables[0].evidence_refs[0]
        return persist_audit_result(audit, context)

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        context.consume_budget("critic_loops")
        return ValidationResult(status=ValidationStatus.PASS, summary="Supported.")

    async def fake_run(agent, agent_input, *, context, **kwargs):  # noqa: ANN001
        return SimpleNamespace(
            final_output=LeadResult(
                objective="Why did profitability decline?",
                answer="A reporting-day gap distorts the comparison.",
                hypotheses=[
                    Hypothesis(
                        id="H2",
                        statement="A data-quality gap distorts the comparison.",
                        status=HypothesisStatus.SUPPORTED,
                        evidence_refs=[audit_reference["ref"]],
                    )
                ],
            )
        )

    monkeypatch.setattr(Runner, "run", fake_run)
    result = asyncio.run(
        AnalysisRunner(
            workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
            auditor_runner=fake_auditor,
            critic_runner=fake_critic,
        ).run(
            "canary-supported",
            "Why did profitability decline?",
            inputs_source=_canary_workspace(tmp_path),
        )
    )

    assert result.status is RunStatus.COMPLETED, result.error
    ledger = AnalysisLedger(result.workspace)
    assert ledger.attempt_history[-1].status is AttemptStatus.COMPLETED
    assert ledger.attempt_history[-1].block_reason is None
    assert ledger.hypotheses[0].evidence_refs == [audit_reference["ref"]]


def test_the_canary_recovers_when_the_correction_cites_real_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retained regression also pins that R23 rescues the fixable case."""

    inputs_source = _canary_workspace(tmp_path)
    audit_reference: dict[str, str] = {}

    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        audit = _evidence_bearing_audit(context)
        audit_reference["ref"] = audit.tables[0].evidence_refs[0]
        return persist_audit_result(audit, context)

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        context.consume_budget("critic_loops")
        return ValidationResult(status=ValidationStatus.PASS, summary="Supported.")

    calls: list[str] = []

    async def fake_run(agent, agent_input, *, context, **kwargs):  # noqa: ANN001
        calls.append(agent.name)
        refs = [_FABRICATED] if len(calls) == 1 else [audit_reference["ref"]]
        return SimpleNamespace(
            final_output=LeadResult(
                objective="Why did profitability decline?",
                answer="A reporting-day gap distorts the comparison.",
                hypotheses=[
                    Hypothesis(
                        id="H2",
                        statement="A data-quality gap distorts the comparison.",
                        status=HypothesisStatus.SUPPORTED,
                        evidence_refs=list(refs),
                    )
                ],
            )
        )

    monkeypatch.setattr(Runner, "run", fake_run)
    result = asyncio.run(
        AnalysisRunner(
            workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
            auditor_runner=fake_auditor,
            critic_runner=fake_critic,
        ).run(
            "canary-corrected",
            "Why did profitability decline?",
            inputs_source=inputs_source,
        )
    )

    assert result.status is RunStatus.COMPLETED, result.error
    assert calls[1].endswith("(evidence correction)")
    ledger = AnalysisLedger(result.workspace)
    assert ledger.hypotheses[0].evidence_refs == [audit_reference["ref"]]
    assert ledger.attempt_history[-1].block_reason is None


def test_the_pre_r20_provenance_free_audit_can_no_longer_be_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The original handoff — an audit with no provenance — is now refused."""

    async def fake_auditor(context, objective, *, agent):  # noqa: ANN001
        return persist_audit_result(
            AuditResult(
                status=AuditStatus.COMPLETE,
                tables=[TableAudit(table_name="orders", row_count=2)],
                limitations=[
                    AuditObservation(statement="Refund reasons are unavailable.")
                ],
                audited_at=_STAMP,
            ),
            context,
        )

    async def fake_critic(context, candidate, *, agent):  # noqa: ANN001
        raise AssertionError("the run must fail before the Critic")

    async def fake_run(agent, agent_input, *, context, **kwargs):  # noqa: ANN001
        raise AssertionError("the run must fail before the Lead")

    monkeypatch.setattr(Runner, "run", fake_run)
    result = asyncio.run(
        AnalysisRunner(
            workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
            auditor_runner=fake_auditor,
            critic_runner=fake_critic,
        ).run(
            "canary-audit-refused",
            "Why did profitability decline?",
            inputs_source=_canary_workspace(tmp_path),
        )
    )

    assert result.status is RunStatus.FAILED
    assert result.block_reason is RunBlockReason.EVIDENCE_PROVENANCE
    assert "audit:table:0" in result.error


# --- the category survives canonical offline rescore -------------------------


def test_the_category_survives_canonical_offline_rescore(tmp_path: Path) -> None:
    """Rescoring must not relabel a provenance failure as something else."""

    from benchmark.runner import BenchmarkRunner
    from evaluation.engine import load_manifest
    from scenarios import discover_scenarios

    registration = next(
        item
        for item in discover_scenarios()
        if item.scenario_id == "meaningful-ab-treatment-effect"
    )
    detail = "lead outputs cite no executed evidence: hypothesis:H2"

    def sources(_registration, destination):  # noqa: ANN001
        inputs = destination / "inputs"
        docs = destination / "docs"
        inputs.mkdir(parents=True, exist_ok=True)
        docs.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"observed_value": [1]}).to_parquet(
            inputs / "customers.parquet", index=False
        )
        (docs / "business_definitions.md").write_text("# Definitions\n", "utf-8")
        return inputs, docs

    def execute(cell, workspace):  # noqa: ANN001
        ledger = AnalysisLedger(
            workspace,
            run_id=cell.run_id,
            objective=cell.scenario.metadata.user_question,
        )
        ledger.begin_attempt()
        ledger.record_elapsed(1.0)
        ledger.mark_failed(
            detail,
            reason=RunBlockReason.EVIDENCE_PROVENANCE,
            detail=detail,
        )
        ledger.finish_attempt("failed", error=detail)
        return SimpleNamespace(
            status=RunStatus.FAILED,
            workspace=workspace,
            state=ledger.state,
            error=detail,
            block_reason=RunBlockReason.EVIDENCE_PROVENANCE,
            block_detail=detail,
        )

    runner = BenchmarkRunner(
        tmp_path / "workspaces",
        architecture_executors={"single-agent": execute},
        source_preparer=sources,
    )
    manifest = runner.build_manifest(
        manifest_id="provenance-taxonomy",
        scenario_ids=[registration.scenario_id],
        architectures=("single-agent",),
        repetitions=1,
        model="offline-fixture",
        model_provider="offline",
        execution_mode=ExecutionMode.DETERMINISTIC,
        repetition_justification="R25 provenance taxonomy fixture",
    )
    manifest_path = tmp_path / "manifest.json"
    runner.persist_plan(manifest, manifest_path)
    executed = runner.execute(manifest_path)
    assert (
        executed.manifest.run_records[0].lifecycle.failure_category
        is FailureCategory.EVIDENCE_PROVENANCE
    )

    rescored_path = tmp_path / "rescored.json"
    rescored = runner.rescore(manifest_path, output_path=rescored_path)
    record = rescored.run_records[0]

    assert record.lifecycle.failure_category is FailureCategory.EVIDENCE_PROVENANCE
    assert record.lifecycle.failure_message is not None
    assert "hypothesis:H2" in record.lifecycle.failure_message
    # The persisted rescored document carries it too, not only the in-memory one.
    assert load_manifest(rescored_path).run_records[0].lifecycle.failure_category is (
        FailureCategory.EVIDENCE_PROVENANCE
    )
    # The workspace's attempt history agrees with the benchmark record.
    ledger = AnalysisLedger(
        Path(record.workspace_path) / "state" / "analysis_ledger.json"
    )
    assert ledger.attempt_history[-1].block_reason is RunBlockReason.EVIDENCE_PROVENANCE
