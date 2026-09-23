"""Offline regressions for persisted calculation output and its aliases."""

import json
from pathlib import Path

import pytest

from agents import AgentRole, inspect_evidence, opaque_workspace_id, run_python, run_sql
from agents.evidence import evidence_events, executed_references
from orchestration.ledger import AnalysisLedger
from sandbox.executor import SandboxExecutionResult
from schemas.run_state import ArtifactKind, ToolEventStatus
from tests.test_agent_runtime import FakeExecutor, _context, _invoke


def _inspect(context, reference, **kwargs):  # noqa: ANN001
    response = _invoke(inspect_evidence, context, {"reference": reference, **kwargs})
    assert response.success, response.error
    return response.data


def test_sql_result_aliases_survive_reload_without_reexecution(tmp_path: Path) -> None:
    context = _context(tmp_path)
    attempt_id = context.ledger.begin_attempt()
    result = _invoke(
        run_sql, context, {"sql": "SELECT 7 * 9 AS total", "query_id": "audit"}
    )
    assert result.success
    event = context.ledger.tool_events[-1]
    assert result.data["tool_event_id"] == event.id
    assert event.output["rows"] == [[63]]
    assert event.attempt_id == attempt_id
    public_attempt_id = attempt_id.replace(
        context.run_config.run_id, opaque_workspace_id(context.run_config.run_id)
    )
    assert result.data["attempt_id"] == public_attempt_id
    context.artifact_manager.register(
        "working/queries/audit.sql", artifact_id="saved-query", kind=ArtifactKind.QUERY
    )
    context.ledger = AnalysisLedger(context.workspace)
    context.run_config = context.run_config.model_copy(
        update={"agent_role": AgentRole.CRITIC}
    )
    before = context.ledger.budget.model_dump()
    for reference in (event.id, "audit", "working/queries/audit.sql", "saved-query"):
        assert reference in executed_references(context.ledger)
        inspected = _inspect(context, reference)
        assert inspected["tool_event_id"] == event.id
        assert inspected["attempt_id"] == public_attempt_id
        expected_public_output = dict(event.output)
        expected_public_output["attempt_id"] = public_attempt_id
        assert inspected["output"] == expected_public_output
        assert inspected["content"] is None
    source = _inspect(context, "working/queries/audit.sql", view="source")
    assert source["content"] == "SELECT 7 * 9 AS total"
    assert source["output"] is None
    assert context.ledger.budget.model_dump() == before
    assert len(context.ledger.tool_events) == 1


@pytest.mark.parametrize("status", [ToolEventStatus.SUCCEEDED, ToolEventStatus.FAILED])
def test_repeated_alias_never_selects_old_success(tmp_path: Path, status) -> None:  # noqa: ANN001
    context = _context(tmp_path, AgentRole.CRITIC)
    context.sql_service.execute("SELECT 3", query_id="shared")
    first = context.ledger.tool_events[-1]
    context.ledger.append_tool_event(
        first.model_copy(
            update={
                "id": "another-execution",
                "status": status,
                "error": "execution failed"
                if status is ToolEventStatus.FAILED
                else None,
                "output": {"rows": [[9]]}
                if status is ToolEventStatus.SUCCEEDED
                else None,
            }
        )
    )
    context.artifact_manager.register(
        "working/queries/shared.sql", artifact_id="registered", kind=ArtifactKind.QUERY
    )
    context.ledger = AnalysisLedger(context.workspace)
    for alias in ("shared", "working/queries/shared.sql", "registered"):
        response = _invoke(inspect_evidence, context, {"reference": alias})
        assert not response.success
        assert "ambiguous" in response.error.message.lower()
        assert alias not in executed_references(context.ledger)
        assert evidence_events(context.ledger, [alias]) == ()
    assert _inspect(context, first.id)["output"] == first.output


def test_failed_paths_inspect_failure_and_source_separately(tmp_path: Path) -> None:
    context = _context(tmp_path, AgentRole.CRITIC)
    context.sql_service.execute("SELECT missing_column", query_id="failed")
    event = context.ledger.tool_events[-1]
    for reference in (event.id, "failed", "working/queries/failed.sql"):
        assert reference not in executed_references(context.ledger)
        data = _inspect(context, reference)
        assert data["status"] == "failed"
        assert data["output"] is None
        assert data["provenance_verified"] is False
    assert _inspect(context, "working/queries/failed.sql", view="source")["content"]


def test_sql_values_are_deterministic_json_across_reload(tmp_path: Path) -> None:
    context = _context(tmp_path)
    sql = """SELECT DATE '2032-03-04' AS day, 12.340::DECIMAL(8,3) AS amount,
        NULL AS absent, TIMESTAMP '2032-03-04 05:06:07' AS stamp,
        TIME '05:06:07' AS clock, TRUE AS flag, [1, NULL, 3] AS items,
        {'a': 2} AS nested, from_hex('00ff') AS binary,
        'NaN'::DOUBLE AS nan, 'Infinity'::DOUBLE AS inf,
        UUID '12345678-1234-5678-1234-567812345678' AS identifier"""
    result = _invoke(run_sql, context, {"sql": sql})
    assert result.success
    event = context.ledger.tool_events[-1]
    expected = [
        [
            "2032-03-04",
            "12.340",
            None,
            "2032-03-04T05:06:07",
            "05:06:07",
            True,
            [1, None, 3],
            {"a": 2},
            {"type": "bytes", "hex": "00ff"},
            {"type": "float", "value": "nan"},
            {"type": "float", "value": "inf"},
            "12345678-1234-5678-1234-567812345678",
        ]
    ]
    assert event.output["rows"] == expected == result.data["rows"]
    assert event.output["column_types"][1] == "DECIMAL(8,3)"
    assert json.loads(json.dumps(event.output, allow_nan=False)) == event.output
    assert AnalysisLedger(context.workspace).tool_events[-1].output == event.output


def test_row_and_byte_limits_remain_visible_and_inspection_is_pageable(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, AgentRole.CRITIC, max_text_chars=256)
    context.sql_service.max_rows = 3
    result = context.sql_service.execute(
        "SELECT i, repeat('x', 200) AS text FROM range(5) t(i)"
    )
    event = context.ledger.tool_events[-1]
    assert event.output["rows"] == result.rows
    assert event.output["truncated"] is True
    assert event.output["row_count"] == 3
    assert event.output["row_count_is_lower_bound"] is True
    chunks = []
    offset = 0
    while True:
        page = _inspect(context, event.id, output_offset=offset)["output"]
        assert page["result_truncated"] is True
        assert len(page["preview_json"]) <= 256
        chunks.append(page["preview_json"])
        offset = page["next_offset"]
        if offset is None:
            break
    assert json.loads("".join(chunks)) == event.output
    large = context.sql_service.execute("SELECT repeat('x', 2000000) AS large")
    output = context.ledger.tool_events[-1].output
    assert large.success
    assert output["truncated"] is True
    assert output["retained_row_count"] == 0
    assert output["row_count"] == 1
    assert output["row_count_is_lower_bound"] is False
    assert len(json.dumps(output).encode()) < 1_100_000


def test_python_returns_same_bounded_stream_as_persisted_result(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.python_service.executor = FakeExecutor(
        SandboxExecutionResult(
            success=True, stdout="z" * 5000, exit_code=0, duration_seconds=0.1
        )
    )
    result = _invoke(
        run_python, context, {"source": "print('calculation')", "script_id": "calc"}
    )
    event = context.ledger.tool_events[-1]
    assert result.data["tool_event_id"] == event.id
    assert result.data["stdout"] == event.output["stdout"]
    assert result.data["stdout_truncated"] is event.output["stdout_truncated"] is True
    context.run_config = context.run_config.model_copy(
        update={"agent_role": AgentRole.CRITIC, "max_text_chars": 10000}
    )
    context.ledger = AnalysisLedger(context.workspace)
    for alias in (event.id, "calc", "working/scripts/calc.py"):
        assert _inspect(context, alias)["output"] == event.output
    assert (
        _inspect(context, "working/scripts/calc.py", view="source")["content"]
        == "print('calculation')"
    )


def test_execution_ids_do_not_collide_between_sql_and_python(tmp_path: Path) -> None:
    context = _context(tmp_path)
    sql = _invoke(run_sql, context, {"sql": "SELECT 4", "query_id": "same"})
    python = _invoke(run_python, context, {"source": "print(5)", "script_id": "same"})
    assert sql.success and python.success
    assert sql.data["tool_event_id"] != python.data["tool_event_id"]
    assert "same" not in executed_references(context.ledger)


@pytest.mark.parametrize("role", list(AgentRole))
def test_every_role_can_inspect_a_prior_calculation(
    tmp_path: Path, role: AgentRole
) -> None:
    context = _context(tmp_path, role)
    context.sql_service.execute("SELECT 17 AS total", query_id="prior")
    assert _inspect(context, "prior")["output"]["rows"] == [[17]]


def test_legacy_result_is_explicitly_unavailable_and_never_recomputed(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, AgentRole.CRITIC)
    context.sql_service.execute("SELECT 11", query_id="legacy")
    event = context.ledger.tool_events[-1]
    context.ledger.tool_events[-1] = event.model_copy(
        update={"id": "tool-legacy", "output": {"columns": ["value"], "row_count": 1}}
    )
    context.ledger.save()
    context.ledger = AnalysisLedger(context.workspace)
    for alias in ("tool-legacy", "legacy", "working/queries/legacy.sql"):
        data = _inspect(context, alias)
        assert data["result_available"] is False
        assert "rows" not in data["output"]
    assert context.ledger.budget.sql_executions == 1


def test_unsupported_constants_still_fail_lineage(tmp_path: Path) -> None:
    from agents.evidence import has_source_lineage

    context = _context(tmp_path)
    result = context.sql_service.execute("SELECT 123 AS invented", query_id="constant")
    for alias in (result.tool_event_id, "constant", "working/queries/constant.sql"):
        assert not has_source_lineage(context.ledger, [alias])


def test_generated_python_alias_and_finding_resolve_execution(tmp_path: Path) -> None:
    from schemas.findings import ConfidenceLevel, Finding

    context = _context(tmp_path, AgentRole.CRITIC)

    class WritingExecutor(FakeExecutor):
        def execute(self, script_path, **kwargs):  # noqa: ANN001
            (context.workspace.outputs / "computed.json").write_text('{"value": 42}')
            return SandboxExecutionResult(
                success=True, stdout='{"value":42}', exit_code=0, duration_seconds=0
            )

    context.python_service.executor = WritingExecutor()
    result = context.python_service.run_python(
        "print('calculation')", script_id="computed"
    )
    event = context.ledger.tool_events[-1]
    context.artifact_manager.register(
        "outputs/computed.json", artifact_id="computed-file"
    )
    context.ledger.upsert_finding(
        Finding(
            id="analyst:calculated",
            statement="A calculation was recorded.",
            evidence_refs=[result.tool_event_id],
            confidence=ConfidenceLevel.HIGH,
        )
    )
    context.ledger = AnalysisLedger(context.workspace)
    for alias in (
        "outputs/computed.json",
        "computed-file",
        "analyst:calculated",
        "calculated",
    ):
        assert _inspect(context, alias)["output"] == event.output
    assert (
        _inspect(context, "computed-file", view="source")["content"] == '{"value": 42}'
    )


def test_python_failure_and_repeated_generated_alias_are_not_success_evidence(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, AgentRole.CRITIC)
    context.ledger.begin_attempt()
    first = context.python_service.run_python("print('one')", script_id="old")
    event = context.ledger.tool_events[-1]
    context.ledger.tool_events[-1] = event.model_copy(
        update={"artifact_refs": [*event.artifact_refs, "outputs/shared.json"]}
    )
    context.ledger.begin_attempt()
    context.python_service.executor = FakeExecutor(
        SandboxExecutionResult(
            success=False,
            stdout="partial",
            stderr="bad calculation",
            exit_code=1,
            duration_seconds=0.1,
        )
    )
    failure = context.python_service.run_python(
        "raise ValueError()", script_id="failed"
    )
    failed_event = context.ledger.tool_events[-1]
    context.ledger.tool_events[-1] = failed_event.model_copy(
        update={"artifact_refs": [*failed_event.artifact_refs, "outputs/shared.json"]}
    )
    context.ledger.save()
    context.ledger = AnalysisLedger(context.workspace)
    for alias in (failure.tool_event_id, "failed", "working/scripts/failed.py"):
        assert alias not in executed_references(context.ledger)
        assert _inspect(context, alias)["status"] == "failed"
    assert "outputs/shared.json" not in executed_references(context.ledger)
    assert not _invoke(
        inspect_evidence, context, {"reference": "outputs/shared.json"}
    ).success
    assert (
        _inspect(context, first.tool_event_id)["attempt_id"]
        != _inspect(context, failure.tool_event_id)["attempt_id"]
    )


def test_artifact_registration_cannot_launder_failed_event_id(tmp_path: Path) -> None:
    context = _context(tmp_path, AgentRole.CRITIC)
    result = context.sql_service.execute("SELECT unknown", query_id="bad")
    file = context.workspace.outputs / "registered.txt"
    file.write_text("unrelated file")
    context.artifact_manager.register(
        "outputs/registered.txt", artifact_id=result.tool_event_id
    )
    assert "outputs/registered.txt" not in executed_references(context.ledger)
    assert _inspect(context, "outputs/registered.txt")["status"] == "failed"


def test_finding_cannot_hide_a_failed_execution_behind_a_success(
    tmp_path: Path,
) -> None:
    from agents.evidence import finding_reference_aliases, resolve_citations
    from schemas.findings import ConfidenceLevel, Finding

    context = _context(tmp_path, AgentRole.CRITIC)
    good = context.sql_service.execute("SELECT 1")
    bad = context.sql_service.execute("SELECT unknown")
    context.ledger.upsert_finding(
        Finding(
            id="mixed",
            statement="Mixed evidence.",
            evidence_refs=[good.tool_event_id, bad.tool_event_id],
            confidence=ConfidenceLevel.LOW,
        )
    )
    resolution = resolve_citations(
        ["mixed"],
        executed_refs=executed_references(context.ledger),
        aliases=finding_reference_aliases(context.ledger),
    )
    assert not resolution.is_supported
    assert not _invoke(inspect_evidence, context, {"reference": "mixed"}).success


def test_actual_input_calculation_is_inspectable_by_downstream_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agents import AgentRunConfig, AgentRunContext
    from agents.evidence import has_source_lineage
    from tests.test_sql import _workspace_with_parquet
    from tools.artifacts import ArtifactManager
    from tools.python import PythonExecutionService
    from tools.sql import DuckDBExecutionService

    workspace = _workspace_with_parquet(tmp_path, "measurements.parquet")
    ledger = AnalysisLedger(workspace, objective="Inspect a saved aggregate.")
    sql_service = DuckDBExecutionService(workspace, ledger)
    result = sql_service.execute(
        "SELECT count(*) AS n, sum(value) AS total FROM measurements",
        query_id="audit-total",
    )
    assert result.rows == [[3, 3]]
    ledger = AnalysisLedger(workspace)
    sql_service = DuckDBExecutionService(workspace, ledger)
    context = AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=sql_service,
        python_service=PythonExecutionService(
            workspace, ledger, executor=FakeExecutor()
        ),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id=workspace.root.name, agent_role=AgentRole.CRITIC
        ),
    )

    def forbid_execution(*args, **kwargs):  # noqa: ANN002, ANN003
        pytest.fail("inspection must never reexecute a calculation")

    monkeypatch.setattr(sql_service, "execute", forbid_execution)
    monkeypatch.setattr(sql_service, "_execute_sql", forbid_execution)
    for alias in (
        result.tool_event_id,
        "audit-total",
        "working/queries/audit-total.sql",
    ):
        assert has_source_lineage(ledger, [alias])
        assert _inspect(context, alias)["output"]["rows"] == [[3, 3]]


def test_interval_map_timezone_and_unicode_serialize_without_loss(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, AgentRole.CRITIC)
    result = context.sql_service.execute(
        "SELECT INTERVAL '2 days 3 seconds 4 microseconds', map([2, 1], ['β', 'α'])"
    )
    assert result.success
    output = context.ledger.tool_events[-1].output
    assert output["rows"][0][0] == {
        "type": "timedelta",
        "days": 2,
        "seconds": 3,
        "microseconds": 4,
    }
    assert output["rows"][0][1] == {"type": "map", "entries": [[1, "α"], [2, "β"]]}
    from datetime import UTC, datetime

    from tools.results import json_result_value

    assert json_result_value(datetime(2031, 1, 2, 3, 4, 5, tzinfo=UTC)) == (
        "2031-01-02T03:04:05+00:00"
    )
    assert AnalysisLedger(context.workspace).tool_events[-1].output == output


def test_services_without_ledger_do_not_advertise_resolvable_event_ids(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    context.sql_service.ledger = None
    context.python_service.ledger = None
    assert context.sql_service.execute("SELECT 1").tool_event_id is None
    assert context.python_service.run_python("print(1)").tool_event_id is None
    assert context.ledger.tool_events == []
