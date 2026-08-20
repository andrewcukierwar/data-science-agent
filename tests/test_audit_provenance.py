"""R20 regressions: typed audit provenance across architecture boundaries.

The 2026-08-20 multi-agent canary failed because a successful Data Audit
produced provenance-free statements, the Lead was handed that audit as
"evidence", and it resolved a hypothesis with the invented reference
``completed_data_audit``. These tests pin the contract that closes that gap:
audit claims carry canonical references, a completed audit cannot persist
unsupported claims, and the Lead receives those references as a bounded typed
catalog rather than having to invent one.
"""

import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents import (
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    AuditClaimKind,
    AuditEvidenceError,
    LeadEvidenceError,
    audit_claims,
    build_audit_evidence_catalog,
    persist_audit_result,
    run_data_auditor,
    validate_audit_provenance,
)
from agents.auditor import DATA_AUDITOR_INSTRUCTIONS
from agents.generalist import persist_generalist_result
from agents.lead import LEAD_INSTRUCTIONS, _lead_input
from agents.model_usage import Runner
from evaluation.contracts import (
    SUPPORTED_WORKSPACE_VERSIONS,
    check_workspace_version_compatibility,
)
from orchestration.ledger import AnalysisLedger
from schemas.audit import (
    AUDIT_CONTRACT_VERSION,
    LEGACY_AUDIT_CONTRACT_VERSION,
    AuditObservation,
    AuditResult,
    AuditStatus,
    DataQualityIssue,
    DateRange,
    IssueSeverity,
    TableAudit,
)
from schemas.findings import ConfidenceLevel, Finding
from schemas.generalist import GeneralistResult
from schemas.lead import LeadResult
from schemas.run_state import (
    CURRENT_STATE_SCHEMA_VERSION,
    Hypothesis,
    HypothesisStatus,
    ToolEvent,
    ToolEventStatus,
)
from schemas.validation import ValidationResult, ValidationStatus
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager

_STAMP = datetime(2026, 1, 1, tzinfo=UTC)
_SQL_EVENT = "tool-Q001"
_SQL_PATH = "working/queries/Q001.sql"
_FAILED_SQL_EVENT = "tool-Q404"


def _context(tmp_path: Path, role: AgentRole = AgentRole.DATA_AUDITOR):
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace("run-audit")
    ledger = AnalysisLedger(workspace, objective="Audit the data.")
    return AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id="run-audit",
            agent_role=role,
            model="test-model",
        ),
    )


def _record_executions(ledger: AnalysisLedger) -> None:
    """Record one successful and one failed SQL execution."""

    ledger.append_tool_event(
        ToolEvent(
            id=_SQL_EVENT,
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=_STAMP,
            completed_at=_STAMP,
            arguments={"query_id": "Q001", "query_path": _SQL_PATH},
            artifact_refs=[_SQL_PATH],
        )
    )
    ledger.append_tool_event(
        ToolEvent(
            id=_FAILED_SQL_EVENT,
            tool_name="run_sql",
            status=ToolEventStatus.FAILED,
            started_at=_STAMP,
            completed_at=_STAMP,
            arguments={"query_id": "Q404", "query_path": "working/queries/Q404.sql"},
            artifact_refs=["working/queries/Q404.sql"],
            error="Catalog Error: table does not exist",
        )
    )


def _audit(refs: list[str], *, status: AuditStatus = AuditStatus.COMPLETE):
    return AuditResult(
        status=status,
        tables=[
            TableAudit(
                table_name="orders",
                row_count=1200,
                date_range=DateRange(start=date(2025, 4, 1), end=date(2025, 6, 30)),
                warnings=[
                    AuditObservation(
                        statement="2025-05-04 has no orders.",
                        evidence_refs=list(refs),
                    )
                ],
                evidence_refs=list(refs),
            )
        ],
        issues=[
            DataQualityIssue(
                id="missing-reporting-day",
                severity=IssueSeverity.MEDIUM,
                message="One reporting day is absent from orders.",
                table_name="orders",
                evidence_refs=list(refs),
            )
        ],
        limitations=[
            AuditObservation(
                statement="Refund reasons are not available in the inputs.",
                evidence_refs=list(refs),
            )
        ],
        audited_at=_STAMP,
    )


# --- material claims carry exact executed references -------------------------


def test_every_material_audit_claim_is_enumerated_with_a_stable_claim_id() -> None:
    claims = audit_claims(_audit([_SQL_EVENT]))

    assert [claim.claim_id for claim in claims] == [
        "audit:table:0",
        "audit:table:0:warning:0",
        "audit:issue:0",
        "audit:limitation:0",
    ]
    assert [claim.kind for claim in claims] == [
        AuditClaimKind.TABLE_PROFILE,
        AuditClaimKind.TABLE_WARNING,
        AuditClaimKind.ISSUE,
        AuditClaimKind.LIMITATION,
    ]
    assert claims[2].issue_id == "missing-reporting-day"


def test_completed_audit_with_executed_references_is_persisted_canonically(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    _record_executions(context.ledger)

    persisted = persist_audit_result(_audit([_SQL_PATH]), context)
    reloaded = AnalysisLedger(context.ledger.state_path)

    assert persisted.tables[0].evidence_refs == [_SQL_PATH]
    assert persisted.limitations[0].evidence_refs == [_SQL_PATH]
    assert reloaded.audit == persisted
    for claim in audit_claims(reloaded.audit):
        assert claim.evidence_refs


def test_specialist_finding_alias_resolves_to_its_executed_reference(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    _record_executions(context.ledger)
    context.ledger.upsert_finding(
        Finding(
            id="analyst:F1",
            statement="Orders fell in the second period.",
            metric="orders",
            value=-0.12,
            evidence_refs=[_SQL_PATH],
            confidence=ConfidenceLevel.HIGH,
        )
    )

    persisted = persist_audit_result(_audit(["F1"]), context)

    assert persisted.issues[0].evidence_refs == [_SQL_PATH]


# --- completed audits cannot persist unsupported material claims -------------


@pytest.mark.parametrize(
    ("refs", "reason"),
    [
        ([], "missing"),
        ([_FAILED_SQL_EVENT], "failed"),
        (["working/queries/Q404.sql"], "failed_artifact_path"),
        (["completed_data_audit"], "fabricated"),
        (["F1"], "ambiguous_alias"),
    ],
)
def test_completed_audit_refuses_unsupported_provenance(
    tmp_path: Path,
    refs: list[str],
    reason: str,
) -> None:
    context = _context(tmp_path)
    _record_executions(context.ledger)
    if reason == "ambiguous_alias":
        for namespace in ("analyst", "statistician"):
            context.ledger.upsert_finding(
                Finding(
                    id=f"{namespace}:F1",
                    statement="Two specialists used the same local label.",
                    metric="orders",
                    value=-0.12,
                    evidence_refs=[_SQL_PATH],
                    confidence=ConfidenceLevel.HIGH,
                )
            )

    with pytest.raises(AuditEvidenceError) as error:
        persist_audit_result(_audit(refs), context)

    assert "audit:table:0" in str(error.value)
    assert "audit:issue:0[missing-reporting-day]" in str(error.value)
    assert "audit:limitation:0" in str(error.value)
    assert context.ledger.audit is None


def test_audit_claim_may_cite_a_verified_artifact_but_not_a_tampered_one(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    _record_executions(context.ledger)
    profile = context.workspace.root / "working" / "orders_profile.csv"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text("table,row_count\norders,1200\n", encoding="utf-8")
    artifact = context.artifact_manager.register_artifact(
        "working/orders_profile.csv",
        artifact_id="A-profile",
    )
    context.ledger.append_tool_event(
        ToolEvent(
            id="tool-profile",
            tool_name="run_python",
            status=ToolEventStatus.SUCCEEDED,
            started_at=_STAMP,
            completed_at=_STAMP,
            arguments={"script_path": "working/scripts/profile.py"},
            artifact_refs=[artifact.path],
        )
    )

    persisted = persist_audit_result(_audit([artifact.id]), context)
    assert persisted.tables[0].evidence_refs == [artifact.id]

    profile.write_text("table,row_count\norders,999999\n", encoding="utf-8")
    with pytest.raises(AuditEvidenceError):
        persist_audit_result(_audit([artifact.id]), context)


def test_unrelated_successful_execution_does_not_rescue_an_audit_claim(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    _record_executions(context.ledger)

    with pytest.raises(AuditEvidenceError, match="audit:limitation:0"):
        persist_audit_result(_audit(["completed_data_audit"]), context)


def test_incomplete_audit_is_validated_and_blocked_audit_is_exempt(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    _record_executions(context.ledger)

    with pytest.raises(AuditEvidenceError):
        validate_audit_provenance(
            _audit([], status=AuditStatus.INCOMPLETE),
            context.ledger,
        )

    blocked = _audit([], status=AuditStatus.BLOCKED)
    assert validate_audit_provenance(blocked, context.ledger) is blocked


def test_data_auditor_run_refuses_a_provenance_free_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    _record_executions(context.ledger)

    async def fake_run(agent, objective, *, context, **kwargs):  # noqa: ANN001
        return SimpleNamespace(final_output=_audit([]))

    monkeypatch.setattr(Runner, "run", fake_run)

    with pytest.raises(AuditEvidenceError):
        asyncio.run(run_data_auditor(context))
    assert context.ledger.audit is None


# --- the Lead receives a bounded typed catalog -------------------------------


def test_audit_evidence_catalog_exposes_canonical_references(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    _record_executions(context.ledger)

    catalog = build_audit_evidence_catalog(_audit([_SQL_EVENT]), context.ledger)

    assert catalog.catalog_version == "1.0"
    assert catalog.audit_status is AuditStatus.COMPLETE
    assert [entry.claim_id for entry in catalog.entries] == [
        "audit:table:0",
        "audit:table:0:warning:0",
        "audit:issue:0",
        "audit:limitation:0",
    ]
    assert catalog.citable_references == (_SQL_EVENT,)
    assert catalog.truncated is False
    for entry in catalog.entries:
        assert entry.evidence_refs == (_SQL_EVENT,)


def test_audit_evidence_catalog_omits_unsupported_claims_and_stays_bounded(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    _record_executions(context.ledger)
    audit = _audit([_SQL_EVENT]).model_copy(
        update={
            "limitations": [
                AuditObservation(
                    statement="Unsupported statement.",
                    evidence_refs=["completed_data_audit"],
                )
            ]
        }
    )

    catalog = build_audit_evidence_catalog(audit, context.ledger)

    assert "audit:limitation:0" not in [entry.claim_id for entry in catalog.entries]
    assert "completed_data_audit" not in catalog.citable_references

    bounded = build_audit_evidence_catalog(audit, context.ledger, entry_limit=1)
    assert len(bounded.entries) == 1
    assert bounded.truncated is True


def test_lead_input_supplies_the_catalog_and_no_pseudo_reference(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    _record_executions(context.ledger)
    audit = _audit([_SQL_EVENT])
    catalog = build_audit_evidence_catalog(audit, context.ledger)

    prompt = _lead_input(
        "Explain the profit change.",
        business_context="Quarterly review.",
        audit=audit,
        audit_evidence=catalog,
    )

    assert "COMPLETED_DATA_AUDIT_JSON" not in prompt
    assert "DATA_AUDIT_EVIDENCE_CATALOG_JSON" in prompt
    assert _SQL_EVENT in prompt
    assert "no reference named completed_data_audit exists" in prompt
    assert "completed_data_audit" in LEAD_INSTRUCTIONS


def test_lead_still_rejects_the_2026_08_20_pseudo_reference(tmp_path: Path) -> None:
    """The bounded catalog is a better input, never a weaker gate."""

    from agents.lead import validate_lead_result

    context = _context(tmp_path, role=AgentRole.LEAD)
    _record_executions(context.ledger)
    persist_audit_result(_audit([_SQL_EVENT]), context)
    candidate = LeadResult(
        objective="Explain the profit change.",
        answer="A reporting-day gap distorts the comparison.",
        hypotheses=[
            Hypothesis(
                id="H2",
                statement="A missing reporting day distorts the comparison.",
                status=HypothesisStatus.SUPPORTED,
                evidence_refs=["completed_data_audit"],
            )
        ],
    )

    with pytest.raises(LeadEvidenceError, match="hypothesis:H2"):
        validate_lead_result(candidate, context.ledger)


def test_lead_can_resolve_an_audit_hypothesis_from_the_catalog(
    tmp_path: Path,
) -> None:
    from agents.lead import validate_lead_result

    context = _context(tmp_path, role=AgentRole.LEAD)
    _record_executions(context.ledger)
    audit = persist_audit_result(_audit([_SQL_EVENT]), context)
    catalog = build_audit_evidence_catalog(audit, context.ledger)
    candidate = LeadResult(
        objective="Explain the profit change.",
        answer="A reporting-day gap distorts the comparison.",
        hypotheses=[
            Hypothesis(
                id="H2",
                statement="A missing reporting day distorts the comparison.",
                status=HypothesisStatus.SUPPORTED,
                evidence_refs=list(catalog.entries[2].evidence_refs),
            )
        ],
    )

    validated = validate_lead_result(candidate, context.ledger)

    assert validated.hypotheses[0].evidence_refs == [_SQL_EVENT]


def test_lead_run_builds_the_catalog_from_the_persisted_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents.lead import run_lead

    context = _context(tmp_path, role=AgentRole.LEAD)
    _record_executions(context.ledger)
    audit = _audit([_SQL_EVENT])
    captured: dict[str, str] = {}

    async def fake_run(agent, prompt, *, context, **kwargs):  # noqa: ANN001
        captured["prompt"] = prompt
        return SimpleNamespace(
            final_output=LeadResult(
                objective="Explain the profit change.",
                answer="A reporting-day gap distorts the comparison.",
            )
        )

    monkeypatch.setattr(Runner, "run", fake_run)
    asyncio.run(run_lead(context, "Explain the profit change.", audit=audit))

    payload = captured["prompt"]
    assert "DATA_AUDIT_EVIDENCE_CATALOG_JSON" in payload
    section = payload.split("DATA_AUDIT_EVIDENCE_CATALOG_JSON:\n")[1]
    catalog = json.loads(section.split("\n\n")[0])
    assert catalog["citable_references"] == [_SQL_EVENT]


# --- architecture equivalence -------------------------------------------------


def test_single_and_multi_agent_audits_expose_equivalent_claim_provenance(
    tmp_path: Path,
) -> None:
    multi = _context(tmp_path / "multi", role=AgentRole.DATA_AUDITOR)
    single = _context(tmp_path / "single", role=AgentRole.GENERALIST)
    for context in (multi, single):
        _record_executions(context.ledger)

    multi_audit = persist_audit_result(_audit([_SQL_PATH]), multi)
    generalist = persist_generalist_result(
        GeneralistResult(
            audit=_audit([_SQL_PATH]),
            candidate=LeadResult(
                objective="Explain the profit change.",
                answer="A reporting-day gap distorts the comparison.",
            ),
            validation=ValidationResult(
                status=ValidationStatus.PASS,
                summary="The candidate is supported by executed evidence.",
            ),
        ),
        single,
    )

    assert audit_claims(multi_audit) == audit_claims(generalist.audit)
    assert (
        build_audit_evidence_catalog(multi_audit, multi.ledger).entries
        == build_audit_evidence_catalog(generalist.audit, single.ledger).entries
    )


def test_generalist_audit_without_provenance_is_refused(tmp_path: Path) -> None:
    context = _context(tmp_path, role=AgentRole.GENERALIST)
    _record_executions(context.ledger)

    with pytest.raises(AuditEvidenceError):
        persist_generalist_result(
            GeneralistResult(
                audit=_audit([]),
                candidate=LeadResult(objective="Explain.", answer="Something."),
                validation=ValidationResult(
                    status=ValidationStatus.PASS,
                    summary="No material issue remains.",
                ),
            ),
            context,
        )
    assert context.ledger.audit is None


# --- versioning and compatibility --------------------------------------------


def test_audit_contract_two_uses_typed_observations() -> None:
    schema = AuditResult.model_json_schema()
    limitation_items = schema["properties"]["limitations"]["items"]
    observation = schema["$defs"]["AuditObservation"]

    assert AUDIT_CONTRACT_VERSION == "2.0"
    assert limitation_items == {"$ref": "#/$defs/AuditObservation"}
    assert set(observation["properties"]) == {"statement", "evidence_refs"}
    table = schema["$defs"]["TableAudit"]
    assert "evidence_refs" in table["properties"]
    assert table["properties"]["warnings"]["items"] == {
        "$ref": "#/$defs/AuditObservation"
    }


def test_output_schema_fingerprint_is_bound_to_the_audit_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import BaseModel

    from benchmark.runner import output_schema_fingerprint

    baseline = output_schema_fingerprint()

    class LegacyAuditResult(BaseModel):
        """Audit contract 1.0: provenance-free limitation strings."""

        status: AuditStatus
        limitations: list[str] = []

    import agents.output_contract as output_contract

    monkeypatch.setattr(
        output_contract,
        "PRODUCTION_AGENT_OUTPUT_TYPES",
        {
            **dict(output_contract.PRODUCTION_AGENT_OUTPUT_TYPES),
            AgentRole.DATA_AUDITOR: LegacyAuditResult,
        },
    )

    assert output_schema_fingerprint() != baseline


def test_new_workspaces_declare_the_audit_contract_state_version(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace("run-v11")
    ledger = AnalysisLedger(workspace, run_id="run-v11", objective="Audit the data.")

    assert CURRENT_STATE_SCHEMA_VERSION == "1.1"
    assert ledger.state.schema_version == CURRENT_STATE_SCHEMA_VERSION
    assert CURRENT_STATE_SCHEMA_VERSION in SUPPORTED_WORKSPACE_VERSIONS
    assert check_workspace_version_compatibility(workspace.root) == "1.1"


def test_contract_one_audits_load_without_fabricated_provenance() -> None:
    legacy = AuditResult.model_validate(
        {
            "status": "complete",
            "tables": [
                {
                    "table_name": "orders",
                    "row_count": 10,
                    "warnings": ["Sparse coverage in May."],
                }
            ],
            "limitations": ["Refund reasons are unavailable."],
            "audited_at": "2026-01-01T00:00:00Z",
        }
    )

    assert LEGACY_AUDIT_CONTRACT_VERSION == "1.0"
    assert legacy.limitations == [
        AuditObservation(statement="Refund reasons are unavailable.")
    ]
    assert legacy.tables[0].warnings[0].evidence_refs == []
    assert all(not claim.evidence_refs for claim in audit_claims(legacy))


def test_inspect_relations_returns_a_citable_tool_event_reference(
    tmp_path: Path,
) -> None:
    """The auditor's table profile has to be provable, not merely asserted."""

    context = _context(tmp_path)
    inspection = context.sql_service.inspect_relations()

    assert inspection.tool_event_id is not None
    assert inspection.tool_event_id in {
        event.id
        for event in context.ledger.tool_events
        if event.status is ToolEventStatus.SUCCEEDED
    }

    audit = _audit([inspection.tool_event_id])
    persisted = persist_audit_result(audit, context)
    assert persisted.tables[0].evidence_refs == [inspection.tool_event_id]


def test_inspect_relations_without_a_ledger_advertises_no_reference(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace("run-noled")

    inspection = DuckDBExecutionService(workspace).inspect_relations()

    assert inspection.tool_event_id is None


def test_auditor_instructions_require_evidence_bearing_claims() -> None:
    instructions = DATA_AUDITOR_INSTRUCTIONS.lower()

    for term in (
        "evidence provenance is mandatory",
        "tool_event_id",
        "query_id",
        "generated_evidence_refs",
        "never invent a reference",
        "completed_data_audit",
    ):
        assert term in instructions
