"""Frozen v1.2 evidence compatibility stays inside offline evaluation."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from agents import AgentRole, inspect_evidence
from agents.evidence import (
    evidence_events as current_evidence_events,
)
from agents.evidence import (
    executed_references as current_executed_references,
)
from agents.evidence import (
    finding_reference_aliases as current_finding_reference_aliases,
)
from agents.evidence import (
    resolve_citations as current_resolve_citations,
)
from evaluation.engine import evaluate_workspace
from evaluation.evidence_semantics import (
    CURRENT_EVIDENCE_SEMANTICS,
    LEGACY_V12_EVIDENCE_SEMANTICS,
    evidence_semantics_for_contract,
)
from evaluation.rules import rules_for_scenario
from orchestration.ledger import AnalysisLedger
from schemas.audit import AuditObservation, AuditResult, AuditStatus, TableAudit
from schemas.findings import ConfidenceLevel, Finding
from schemas.run_state import ArtifactKind, ToolEventStatus
from tests.test_agent_runtime import _context, _invoke
from tests.test_sql import _workspace_with_parquet
from tools.sql import DuckDBExecutionService

ROOT = Path(__file__).resolve().parents[1]
V8_MANIFEST = ROOT / ".runs/phase2-task10-20260820-v8/benchmark.json"
V8_RESCORING = ROOT / ".runs/phase2-task10-20260820-v8/benchmark-rescored.json"


def _reused_successes(tmp_path: Path):  # noqa: ANN202
    context = _context(tmp_path, AgentRole.CRITIC)
    first = context.sql_service.execute("SELECT 1 AS n", query_id="reused")
    second = context.ledger.tool_events[-1].model_copy(
        update={"id": "second-successful-execution"}
    )
    context.ledger.append_tool_event(second)
    context.ledger = AnalysisLedger(context.workspace)
    assert first.success and second.status is ToolEventStatus.SUCCEEDED
    return context, first, second, "working/queries/reused.sql"


def test_frozen_legacy_module_matches_b7ca12c_implementation() -> None:
    """Keep every frozen implementation function identical to its source."""

    frozen = subprocess.run(
        ["git", "show", "b7ca12c:src/agents/evidence.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    compatibility_copy = (ROOT / "src/evaluation/legacy_v12_evidence.py").read_text(
        encoding="utf-8"
    )

    def implementation_ast(source: str) -> str:
        module = ast.parse(source)
        if (
            module.body
            and isinstance(module.body[0], ast.Expr)
            and isinstance(module.body[0].value, ast.Constant)
            and isinstance(module.body[0].value.value, str)
        ):
            module.body.pop(0)
        return ast.dump(module, include_attributes=False)

    assert implementation_ast(compatibility_copy) == implementation_ast(frozen)


def test_v12_reused_success_alias_matches_frozen_contract(tmp_path: Path) -> None:
    context, first, second, path = _reused_successes(tmp_path)
    legacy = LEGACY_V12_EVIDENCE_SEMANTICS
    strict = CURRENT_EVIDENCE_SEMANTICS

    assert path in legacy.executed_references(context.ledger)
    assert path not in strict.executed_references(context.ledger)
    assert len(legacy_v12_events(context.ledger, [path])) == 2
    assert current_evidence_events(context.ledger, [path]) == ()
    assert legacy_v12_citations(context.ledger, [path]).is_supported
    assert legacy_v12_citations(context.ledger, [first.tool_event_id]).is_supported
    assert legacy_v12_citations(context.ledger, [second.id]).is_supported


def test_v12_success_and_failure_reuse_and_finding_aliases_are_frozen(
    tmp_path: Path,
) -> None:
    context, successful, _, path = _reused_successes(tmp_path)
    failure = context.ledger.tool_events[-1].model_copy(
        update={
            "id": "failed-reuse",
            "status": ToolEventStatus.FAILED,
            "error": "historical failed execution",
            "output": None,
        }
    )
    context.ledger.append_tool_event(failure)
    context.ledger.upsert_finding(
        Finding(
            id="finding:partial",
            statement="Historical partial finding alias.",
            evidence_refs=[path, failure.id],
            confidence=ConfidenceLevel.LOW,
        )
    )
    context.ledger = AnalysisLedger(context.workspace)
    legacy = LEGACY_V12_EVIDENCE_SEMANTICS
    refs = legacy.executed_references(context.ledger)

    assert path in refs
    assert successful.tool_event_id in refs
    assert failure.id not in refs
    resolved = legacy_v12_citations(context.ledger, ["partial"])
    assert resolved.is_supported
    assert resolved.resolved == (path,)
    assert not current_resolve_citations(
        ["partial"],
        executed_refs=current_executed_references(context.ledger),
        aliases=current_finding_reference_aliases(context.ledger),
    ).is_supported


def test_v12_artifact_id_maps_back_to_all_successful_path_events(
    tmp_path: Path,
) -> None:
    context, _, _, path = _reused_successes(tmp_path)
    artifact = context.artifact_manager.register(
        path, artifact_id="registered-query", kind=ArtifactKind.QUERY
    )
    context.ledger = AnalysisLedger(context.workspace)

    assert artifact.path == path
    assert "registered-query" in LEGACY_V12_EVIDENCE_SEMANTICS.executed_references(
        context.ledger
    )
    assert len(legacy_v12_events(context.ledger, ["registered-query"])) == 2
    assert "registered-query" not in CURRENT_EVIDENCE_SEMANTICS.executed_references(
        context.ledger
    )


def test_historical_audit_and_current_runtime_keep_separate_contracts(
    tmp_path: Path,
) -> None:
    context, _, _, path = _reused_successes(tmp_path)
    audit = AuditResult(
        status=AuditStatus.COMPLETE,
        tables=[TableAudit(table_name="orders", row_count=1, evidence_refs=[path])],
        limitations=[
            AuditObservation(
                statement="Repeated historical alias.", evidence_refs=[path]
            )
        ],
    )
    context.ledger.record_audit(audit)
    context.ledger.upsert_finding(
        Finding(
            id="F-REUSED",
            statement="A finding backed by the reused path.",
            evidence_refs=[path],
            confidence=ConfidenceLevel.LOW,
        )
    )
    context.ledger = AnalysisLedger(context.workspace)

    legacy_rules = rules_for_scenario("meaningful-ab-treatment-effect", "1.0")
    assert (legacy_rules.scenario_version, legacy_rules.evaluator_version) == (
        "1.0",
        "1.2",
    )
    legacy_result = evaluate_workspace(context.workspace, legacy_rules).result
    legacy_checks = {check.check_id: check for check in legacy_result.checks}
    assert legacy_checks["provenance:finding:F-REUSED"].status.value == "pass"
    assert legacy_checks["capability:data_audit"].status.value == "pass"
    assert legacy_checks["data_quality:claim_provenance"].status.value == "pass"
    assert all(
        item.supported
        for item in LEGACY_V12_EVIDENCE_SEMANTICS.resolve_material_claims(
            (
                ("metric_comparison:orders", (path,)),
                ("statistical_assessment:orders", (path,)),
            ),
            context.ledger,
        )
    )

    current_rules = rules_for_scenario("meaningful-ab-treatment-effect", "1.1")
    assert (current_rules.scenario_version, current_rules.evaluator_version) == (
        "1.1",
        "1.3",
    )
    assert (
        evidence_semantics_for_contract(
            scenario_version=current_rules.scenario_version,
            evaluator_version=current_rules.evaluator_version,
        )
        is CURRENT_EVIDENCE_SEMANTICS
    )
    current_result = evaluate_workspace(context.workspace, current_rules).result
    current_checks = {check.check_id: check for check in current_result.checks}
    assert current_checks["provenance:finding:F-REUSED"].status.value == "fail"

    inspected = _invoke(inspect_evidence, context, {"reference": path})
    assert not inspected.success
    assert "ambiguous" in inspected.error.message.lower()


def test_legacy_selector_uses_only_the_declared_historical_version_pair() -> None:
    assert (
        evidence_semantics_for_contract(scenario_version="1.0", evaluator_version="1.2")
        is LEGACY_V12_EVIDENCE_SEMANTICS
    )
    for scenario_version, evaluator_version in (
        ("1.1", "1.3"),
        ("1.0", "1.3"),
        ("1.1", "1.2"),
    ):
        assert (
            evidence_semantics_for_contract(
                scenario_version=scenario_version,
                evaluator_version=evaluator_version,
            )
            is CURRENT_EVIDENCE_SEMANTICS
        )


def test_v12_source_lineage_uses_frozen_successful_path_intersection(
    tmp_path: Path,
) -> None:
    workspace = _workspace_with_parquet(tmp_path, "orders.parquet")
    ledger = AnalysisLedger(workspace, objective="Check source lineage aliases.")
    sql_service = DuckDBExecutionService(workspace, ledger)
    for _ in range(2):
        if not ledger.tool_events:
            result = sql_service.execute(
                "SELECT count(*) AS n FROM orders", query_id="lineage-reuse"
            )
            assert result.success
        else:
            duplicate = ledger.tool_events[-1].model_copy(
                update={"id": "second-lineage-execution"}
            )
            ledger.append_tool_event(duplicate)
    ledger = AnalysisLedger(workspace)
    path = "working/queries/lineage-reuse.sql"

    assert LEGACY_V12_EVIDENCE_SEMANTICS.has_source_lineage(ledger, [path])
    assert not CURRENT_EVIDENCE_SEMANTICS.has_source_lineage(ledger, [path])


def legacy_v12_events(ledger: AnalysisLedger, references: list[str]):
    from evaluation.legacy_v12_evidence import evidence_events

    return evidence_events(ledger, references)


def legacy_v12_citations(ledger: AnalysisLedger, references: list[str]):
    from evaluation.legacy_v12_evidence import (
        executed_references,
        finding_reference_aliases,
        resolve_citations,
    )

    return resolve_citations(
        references,
        executed_refs=executed_references(ledger),
        aliases=finding_reference_aliases(ledger),
    )
