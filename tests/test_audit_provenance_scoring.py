"""R21 regressions: audit provenance is enforced by the offline evaluator.

R20 stopped an unsupported audit from being persisted at runtime. That is not
enough on its own: offline scoring is the benchmark's source of truth, it runs
against workspaces the current runtime never touched, and it previously treated
a completed ``AuditResult`` and a matching issue ID as sufficient. These tests
pin the evaluator to the same successful-execution and verified-artifact
boundary the rest of the evidence contract uses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from evaluation.contracts import EvaluationCheck, EvaluationCheckStatus
from evaluation.engine import ScenarioRules, evaluate_workspace
from evaluation.primitives import (
    AnalyticalCapability,
    CapabilityPolicy,
    DataQualityPolicy,
    evaluate_capabilities,
    evaluate_data_quality,
    resolve_audit_claims,
)
from evaluation.rules import rules_for_scenario
from orchestration.ledger import AnalysisLedger
from schemas.audit import (
    AuditObservation,
    AuditResult,
    AuditStatus,
    DataQualityIssue,
    IssueSeverity,
    TableAudit,
)
from schemas.run_state import (
    AgentEvent,
    AgentEventStatus,
    ToolEvent,
    ToolEventStatus,
)
from tools.artifacts import ArtifactManager
from tools.workspace import Workspace, WorkspaceManager

_STAMP = datetime(2026, 1, 1, tzinfo=UTC)
_ISSUE_ID = "missing-reporting-day"

_GOOD_SQL_EVENT = "tool-Q001"
_GOOD_SQL_PATH = "working/queries/Q001.sql"
_GOOD_PY_EVENT = "tool-S001"
_GOOD_PY_PATH = "working/scripts/S001.py"
_INSPECT_EVENT = "tool-inspect-relations-fixture"
_FAILED_SQL_EVENT = "tool-Q404"
_FAILED_SQL_PATH = "working/queries/Q404.sql"
_FAILED_PY_EVENT = "tool-S404"
_FAILED_PY_PATH = "working/scripts/S404.py"
_VERIFIED_ARTIFACT = "A-profile"
_VERIFIED_ARTIFACT_PATH = "working/orders_profile.csv"
_DELETED_ARTIFACT = "A-deleted"
_DELETED_ARTIFACT_PATH = "working/deleted_profile.csv"


@pytest.fixture(name="fixture")
def _fixture(tmp_path: Path) -> tuple[Workspace, AnalysisLedger]:
    """A workspace holding every provenance shape the evaluator must separate."""

    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace("run-r21")
    ledger = AnalysisLedger(workspace, objective="Audit the inputs.")

    for relative in (_GOOD_SQL_PATH, _FAILED_SQL_PATH):
        path = workspace.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SELECT count(*) FROM orders;\n", encoding="utf-8")
    for relative in (_GOOD_PY_PATH, _FAILED_PY_PATH):
        path = workspace.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("import pandas as pd\n", encoding="utf-8")

    ledger.append_tool_event(
        ToolEvent(
            id=_GOOD_SQL_EVENT,
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=_STAMP,
            completed_at=_STAMP,
            arguments={"query_id": "Q001", "query_path": _GOOD_SQL_PATH},
            artifact_refs=[_GOOD_SQL_PATH],
        )
    )
    ledger.append_tool_event(
        ToolEvent(
            id=_GOOD_PY_EVENT,
            tool_name="run_python",
            status=ToolEventStatus.SUCCEEDED,
            started_at=_STAMP,
            completed_at=_STAMP,
            arguments={"script_id": "S001", "script_path": _GOOD_PY_PATH},
            artifact_refs=[_GOOD_PY_PATH],
        )
    )
    ledger.append_tool_event(
        ToolEvent(
            id=_INSPECT_EVENT,
            tool_name="inspect_relations",
            status=ToolEventStatus.SUCCEEDED,
            started_at=_STAMP,
            completed_at=_STAMP,
            arguments={"include_row_counts": True},
        )
    )
    ledger.append_tool_event(
        ToolEvent(
            id=_FAILED_SQL_EVENT,
            tool_name="run_sql",
            status=ToolEventStatus.FAILED,
            started_at=_STAMP,
            completed_at=_STAMP,
            arguments={"query_id": "Q404", "query_path": _FAILED_SQL_PATH},
            artifact_refs=[_FAILED_SQL_PATH],
            error="Catalog Error: table does not exist",
        )
    )
    ledger.append_tool_event(
        ToolEvent(
            id=_FAILED_PY_EVENT,
            tool_name="run_python",
            status=ToolEventStatus.FAILED,
            started_at=_STAMP,
            completed_at=_STAMP,
            arguments={"script_id": "S404", "script_path": _FAILED_PY_PATH},
            artifact_refs=[_FAILED_PY_PATH],
            error="ModuleNotFoundError: no module named 'nope'",
        )
    )

    artifacts = ArtifactManager(workspace, ledger)
    for artifact_id, relative in (
        (_VERIFIED_ARTIFACT, _VERIFIED_ARTIFACT_PATH),
        (_DELETED_ARTIFACT, _DELETED_ARTIFACT_PATH),
    ):
        path = workspace.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("table,row_count\norders,1200\n", encoding="utf-8")
        artifacts.register_artifact(relative, artifact_id=artifact_id)
    (workspace.root / _DELETED_ARTIFACT_PATH).unlink()

    return workspace, ledger


def _executed_refs(workspace: Workspace) -> set[str]:
    from agents.evidence import executed_references

    return executed_references(AnalysisLedger(workspace))


def _audit(
    *,
    issue_refs: list[str] | None = None,
    table_refs: list[str] | None = None,
    limitation_refs: list[str] | None = None,
    status: AuditStatus = AuditStatus.COMPLETE,
) -> AuditResult:
    tables = (
        [
            TableAudit(
                table_name="orders",
                row_count=1200,
                evidence_refs=list(table_refs),
            )
        ]
        if table_refs is not None
        else []
    )
    issues = (
        [
            DataQualityIssue(
                id=_ISSUE_ID,
                severity=IssueSeverity.MEDIUM,
                message="One reporting day is absent from orders.",
                table_name="orders",
                evidence_refs=list(issue_refs),
            )
        ]
        if issue_refs is not None
        else []
    )
    limitations = (
        [
            AuditObservation(
                statement="Refund reasons are not available in the inputs.",
                evidence_refs=list(limitation_refs),
            )
        ]
        if limitation_refs is not None
        else []
    )
    return AuditResult(
        status=status,
        tables=tables,
        issues=issues,
        limitations=limitations,
        audited_at=_STAMP,
    )


def _check(checks: tuple[EvaluationCheck, ...], check_id: str) -> EvaluationCheck:
    return next(check for check in checks if check.check_id == check_id)


def _state(ledger: AnalysisLedger, audit: AuditResult | None):
    if audit is not None:
        ledger.record_audit(audit)
    return AnalysisLedger(ledger.state_path).state


# --- the data-audit capability needs typed outputs and their provenance ------


@pytest.mark.parametrize(
    ("audit_kwargs", "status", "passes", "expected_message_fragment"),
    [
        (None, None, False, "missing"),
        ({}, AuditStatus.INCOMPLETE, False, "expected complete"),
        ({}, AuditStatus.COMPLETE, False, "states no material claim"),
        (
            {"table_refs": ["fabricated-reference"]},
            AuditStatus.COMPLETE,
            False,
            "without executed evidence",
        ),
        (
            {"table_refs": [_GOOD_SQL_PATH]},
            AuditStatus.COMPLETE,
            True,
            "executed provenance",
        ),
    ],
)
def test_data_audit_capability_requires_typed_outputs_and_provenance(
    fixture: tuple[Workspace, AnalysisLedger],
    audit_kwargs: dict | None,
    status: AuditStatus | None,
    passes: bool,
    expected_message_fragment: str,
) -> None:
    workspace, ledger = fixture
    audit = None if audit_kwargs is None else _audit(**audit_kwargs, status=status)
    state = _state(ledger, audit)

    checks = evaluate_capabilities(
        workspace,
        state,
        CapabilityPolicy(required=(AnalyticalCapability.DATA_AUDIT,)),
        executed_refs=_executed_refs(workspace),
    )
    check = _check(checks, "capability:data_audit")

    assert (check.status is EvaluationCheckStatus.PASS) is passes
    assert expected_message_fragment in check.message


# --- required issue IDs must resolve, not merely appear ----------------------


@pytest.mark.parametrize(
    ("refs", "reason"),
    [
        ([], "no references at all"),
        ([_FAILED_SQL_EVENT], "failed SQL tool event"),
        ([_FAILED_SQL_PATH], "failed SQL query path"),
        ([_FAILED_PY_EVENT], "failed Python tool event"),
        ([_FAILED_PY_PATH], "failed Python script path"),
        ([_DELETED_ARTIFACT], "registered artifact whose file is gone"),
        ([_DELETED_ARTIFACT_PATH], "path of a missing artifact file"),
        (["completed_data_audit"], "fabricated reference"),
        (["outputs/never_written.csv"], "reference to a file that never existed"),
    ],
)
def test_required_issue_id_without_executed_evidence_fails_offline(
    fixture: tuple[Workspace, AnalysisLedger],
    refs: list[str],
    reason: str,
) -> None:
    workspace, ledger = fixture
    state = _state(
        ledger,
        _audit(issue_refs=refs, table_refs=[_GOOD_SQL_PATH]),
    )

    checks = evaluate_data_quality(
        state,
        DataQualityPolicy(
            required_issue_ids=(_ISSUE_ID,),
            maximum_issue_severity=IssueSeverity.HIGH,
        ),
        executed_refs=_executed_refs(workspace),
    )

    # The issue ID is present, which is exactly what used to be sufficient.
    assert _check(checks, f"data_quality:required:{_ISSUE_ID}").status is (
        EvaluationCheckStatus.PASS
    ), reason
    assert _check(checks, f"data_quality:required_provenance:{_ISSUE_ID}").status is (
        EvaluationCheckStatus.FAIL
    ), reason
    assert _check(checks, "data_quality:claim_provenance").status is (
        EvaluationCheckStatus.FAIL
    ), reason


@pytest.mark.parametrize(
    "reference",
    [_GOOD_SQL_EVENT, _GOOD_SQL_PATH, _GOOD_PY_PATH, _INSPECT_EVENT],
)
def test_required_issue_id_with_executed_evidence_passes(
    fixture: tuple[Workspace, AnalysisLedger],
    reference: str,
) -> None:
    workspace, ledger = fixture
    state = _state(
        ledger,
        _audit(issue_refs=[reference], table_refs=[reference]),
    )

    checks = evaluate_data_quality(
        state,
        DataQualityPolicy(
            required_issue_ids=(_ISSUE_ID,),
            maximum_issue_severity=IssueSeverity.HIGH,
        ),
        executed_refs=_executed_refs(workspace),
    )

    assert all(check.status is EvaluationCheckStatus.PASS for check in checks)


def test_unrelated_successful_execution_cannot_rescue_an_audit_claim(
    fixture: tuple[Workspace, AnalysisLedger],
) -> None:
    """The run has three successful executions; the claim still cites none."""

    workspace, ledger = fixture
    executed_refs = _executed_refs(workspace)
    assert {_GOOD_SQL_EVENT, _GOOD_PY_EVENT, _INSPECT_EVENT} <= executed_refs

    state = _state(
        ledger,
        _audit(issue_refs=["audit_was_performed"], table_refs=[_GOOD_SQL_PATH]),
    )
    checks = evaluate_data_quality(
        state,
        DataQualityPolicy(
            required_issue_ids=(_ISSUE_ID,),
            maximum_issue_severity=IssueSeverity.HIGH,
        ),
        executed_refs=executed_refs,
    )

    assert _check(checks, f"data_quality:required_provenance:{_ISSUE_ID}").status is (
        EvaluationCheckStatus.FAIL
    )
    resolved = resolve_audit_claims(state.audit, executed_refs)
    assert [item.supported for item in resolved] == [True, False]


# --- a clean audit proves its checks without prescribing a tool or role ------


@pytest.mark.parametrize(
    ("reference", "producer"),
    [
        (_GOOD_SQL_EVENT, "sql"),
        (_GOOD_PY_PATH, "python"),
        (_INSPECT_EVENT, "metadata inspection"),
        (_VERIFIED_ARTIFACT, "verified artifact"),
    ],
)
def test_clean_audit_evidence_is_tool_and_role_neutral(
    fixture: tuple[Workspace, AnalysisLedger],
    reference: str,
    producer: str,
) -> None:
    workspace, ledger = fixture
    state = _state(ledger, _audit(table_refs=[reference]))

    checks = evaluate_data_quality(
        state,
        DataQualityPolicy(
            maximum_issue_severity=IssueSeverity.LOW,
            forbid_any_issues=True,
        ),
        executed_refs=_executed_refs(workspace),
    )

    assert all(check.status is EvaluationCheckStatus.PASS for check in checks), producer


def test_clean_audit_with_a_supported_limitation_alone_passes(
    fixture: tuple[Workspace, AnalysisLedger],
) -> None:
    """Stating what could not be checked is itself proof a check was attempted."""

    workspace, ledger = fixture
    state = _state(ledger, _audit(limitation_refs=[_INSPECT_EVENT]))

    checks = evaluate_data_quality(
        state,
        DataQualityPolicy(
            maximum_issue_severity=IssueSeverity.LOW,
            forbid_any_issues=True,
        ),
        executed_refs=_executed_refs(workspace),
    )

    assert _check(checks, "data_quality:clean_audit_evidence").status is (
        EvaluationCheckStatus.PASS
    )


def test_clean_audit_reporting_nothing_at_all_fails(
    fixture: tuple[Workspace, AnalysisLedger],
) -> None:
    """An empty audit is an absence of findings, not evidence of a clean dataset."""

    workspace, ledger = fixture
    state = _state(ledger, _audit())

    checks = evaluate_data_quality(
        state,
        DataQualityPolicy(
            maximum_issue_severity=IssueSeverity.LOW,
            forbid_any_issues=True,
        ),
        executed_refs=_executed_refs(workspace),
    )

    assert _check(checks, "data_quality:no_issues").status is (
        EvaluationCheckStatus.PASS
    )
    assert _check(checks, "data_quality:clean_audit_evidence").status is (
        EvaluationCheckStatus.FAIL
    )


# --- architecture equivalence -------------------------------------------------


def _architecture_workspace(
    tmp_path: Path,
    *,
    name: str,
    roles: tuple[str, ...],
    audit: AuditResult,
) -> tuple[Workspace, AnalysisLedger]:
    workspace = WorkspaceManager(tmp_path / name).create_workspace(f"run-{name}")
    ledger = AnalysisLedger(workspace, objective="Audit the inputs.")
    query_path = workspace.root / _GOOD_SQL_PATH
    query_path.parent.mkdir(parents=True, exist_ok=True)
    query_path.write_text("SELECT count(*) FROM orders;\n", encoding="utf-8")
    ledger.append_tool_event(
        ToolEvent(
            id=_GOOD_SQL_EVENT,
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=_STAMP,
            completed_at=_STAMP,
            arguments={"query_id": "Q001", "query_path": _GOOD_SQL_PATH},
            artifact_refs=[_GOOD_SQL_PATH],
        )
    )
    for role in roles:
        ledger.append_agent_event(
            AgentEvent(
                id=f"agent-{role}",
                agent_name=role.replace("_", " ").title(),
                agent_role=role,
                status=AgentEventStatus.SUCCEEDED,
                started_at=_STAMP,
                completed_at=_STAMP,
                model="fixture-model",
                objective="Audit the inputs.",
            )
        )
    ledger.record_audit(audit)
    return workspace, AnalysisLedger(ledger.state_path)


def test_semantically_identical_audits_score_identically_across_architectures(
    tmp_path: Path,
) -> None:
    audit = _audit(issue_refs=[_GOOD_SQL_PATH], table_refs=[_GOOD_SQL_PATH])
    policy = DataQualityPolicy(
        required_issue_ids=(_ISSUE_ID,),
        maximum_issue_severity=IssueSeverity.HIGH,
    )
    capability_policy = CapabilityPolicy(required=(AnalyticalCapability.DATA_AUDIT,))

    multi_workspace, multi_ledger = _architecture_workspace(
        tmp_path,
        name="multi-agent",
        roles=("data_auditor", "lead", "analyst", "statistician", "critic"),
        audit=audit,
    )
    single_workspace, single_ledger = _architecture_workspace(
        tmp_path,
        name="single-agent",
        roles=("generalist",),
        audit=audit,
    )

    def scored(workspace: Workspace, ledger: AnalysisLedger):
        executed_refs = _executed_refs(workspace)
        return (
            evaluate_data_quality(ledger.state, policy, executed_refs=executed_refs),
            evaluate_capabilities(
                workspace,
                ledger.state,
                capability_policy,
                executed_refs=executed_refs,
            ),
        )

    multi_quality, multi_capability = scored(multi_workspace, multi_ledger)
    single_quality, single_capability = scored(single_workspace, single_ledger)

    assert multi_quality == single_quality
    assert multi_capability == single_capability
    assert all(
        check.status is EvaluationCheckStatus.PASS
        for check in (*multi_quality, *multi_capability)
    )


def test_unsupported_audit_fails_identically_across_architectures(
    tmp_path: Path,
) -> None:
    audit = _audit(
        issue_refs=["completed_data_audit"],
        table_refs=["completed_data_audit"],
    )
    policy = DataQualityPolicy(
        required_issue_ids=(_ISSUE_ID,),
        maximum_issue_severity=IssueSeverity.HIGH,
    )

    _, multi_ledger = _architecture_workspace(
        tmp_path,
        name="multi-agent",
        roles=("data_auditor", "lead", "analyst", "statistician", "critic"),
        audit=audit,
    )
    single_workspace, single_ledger = _architecture_workspace(
        tmp_path,
        name="single-agent",
        roles=("generalist",),
        audit=audit,
    )
    executed_refs = _executed_refs(single_workspace)

    multi_checks = evaluate_data_quality(
        multi_ledger.state, policy, executed_refs=executed_refs
    )
    single_checks = evaluate_data_quality(
        single_ledger.state, policy, executed_refs=executed_refs
    )

    assert multi_checks == single_checks
    assert (
        _check(multi_checks, f"data_quality:required_provenance:{_ISSUE_ID}").status
        is EvaluationCheckStatus.FAIL
    )


# --- the engine wiring and the deliberate version advance --------------------


def test_offline_engine_applies_audit_provenance_end_to_end(
    fixture: tuple[Workspace, AnalysisLedger],
) -> None:
    workspace, ledger = fixture
    rules = ScenarioRules(
        scenario_id="fixture-scenario",
        scenario_version="1.0",
        evaluator_version="1.2",
        data_quality_policy=DataQualityPolicy(
            required_issue_ids=(_ISSUE_ID,),
            maximum_issue_severity=IssueSeverity.HIGH,
        ),
        capability_policy=CapabilityPolicy(required=(AnalyticalCapability.DATA_AUDIT,)),
    )

    _state(ledger, _audit(issue_refs=[_FAILED_SQL_EVENT], table_refs=[_GOOD_SQL_PATH]))
    unsupported = evaluate_workspace(workspace, rules).checks
    assert (
        _check(unsupported, f"data_quality:required_provenance:{_ISSUE_ID}").status
        is EvaluationCheckStatus.FAIL
    )
    assert _check(unsupported, "capability:data_audit").status is (
        EvaluationCheckStatus.FAIL
    )

    _state(ledger, _audit(issue_refs=[_GOOD_SQL_EVENT], table_refs=[_GOOD_SQL_PATH]))
    supported = evaluate_workspace(workspace, rules).checks
    assert (
        _check(supported, f"data_quality:required_provenance:{_ISSUE_ID}").status
        is EvaluationCheckStatus.PASS
    )
    assert _check(supported, "capability:data_audit").status is (
        EvaluationCheckStatus.PASS
    )


def test_catalog_evaluator_version_carries_the_audit_provenance_change() -> None:
    """The scoring change is versioned rather than applied silently."""

    rules = rules_for_scenario("missing-reporting-day", "1.0")

    assert rules.evaluator_version == "1.2"
    assert AnalyticalCapability.DATA_AUDIT in rules.capability_policy.required


def test_artifact_backed_audit_claim_follows_the_verification_boundary(
    fixture: tuple[Workspace, AnalysisLedger],
) -> None:
    """A registered artifact is provenance only while its file still verifies."""

    workspace, ledger = fixture
    policy = DataQualityPolicy(
        maximum_issue_severity=IssueSeverity.LOW,
        forbid_any_issues=True,
    )

    state = _state(ledger, _audit(table_refs=[_VERIFIED_ARTIFACT]))
    verified = evaluate_data_quality(
        state, policy, executed_refs=_executed_refs(workspace)
    )
    assert _check(verified, "data_quality:claim_provenance").status is (
        EvaluationCheckStatus.PASS
    )

    (workspace.root / _VERIFIED_ARTIFACT_PATH).write_text(
        "table,row_count\norders,999999\n", encoding="utf-8"
    )
    tampered = evaluate_data_quality(
        state, policy, executed_refs=_executed_refs(workspace)
    )
    assert _check(tampered, "data_quality:claim_provenance").status is (
        EvaluationCheckStatus.FAIL
    )
    assert _check(tampered, "data_quality:clean_audit_evidence").status is (
        EvaluationCheckStatus.FAIL
    )
