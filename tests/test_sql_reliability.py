"""P0.3 regressions: independent profiles and actual native cancellation."""

import os
import subprocess
import sys
import threading
import time
from datetime import date, datetime

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from agents import AgentRole, inspect_evidence, inspect_relations
from agents.evidence import executed_references
from agents.result_binding import ResultBindingError, resolve_result
from benchmark.runner import BenchmarkRunner, canonical_manifest_declaration_digest
from orchestration.ledger import AnalysisLedger
from schemas.computation import ComputationBinding, ComputedField
from schemas.metrics import MetricComparison
from tests.test_agent_runtime import _invoke
from tests.test_result_binding import _context
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager

EXPENSIVE_SQL = "SELECT sum(sin(i)) FROM range(100000000000) t(i)"


@pytest.fixture
def profile_service(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    # Retained failure shape: independent facts with wildly different sizes.
    # Their Cartesian product is never executed, even by the test oracle.
    for name, size, days in (
        ("tiny", 3, [date(2031, 4, 2), None, date(2031, 4, 9)]),
        ("large", 100_000, [date(2031, 4, 1), date(2031, 4, 12)]),
    ):
        pq.write_table(
            pa.table(
                {
                    "happened_on": [days[i % len(days)] for i in range(size)],
                    "loaded_at": [datetime(2031, 4, 15)] * size,
                    "amount": list(range(size)),
                }
            ),
            source / f"{name}.parquet",
        )
    pq.write_table(pa.table({"label": ["x", None]}), source / "labels.parquet")
    workspace = WorkspaceManager(tmp_path / "runs").create_workspace(
        "profile", inputs_source=source
    )
    return DuckDBExecutionService(
        workspace, AnalysisLedger(workspace, objective="profile")
    )


def test_independent_schema_driven_temporal_profiles(profile_service, monkeypatch):
    queries = []
    original = duckdb.connect

    class Connection:
        def __init__(self):
            self.inner = original()

        def execute(self, sql, *args, **kwargs):
            queries.append(sql)
            return self.inner.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(duckdb, "connect", lambda **kw: Connection())
    result = profile_service.inspect_relations(include_missingness=True)
    relations = {r.relation_name: r for r in result.relations}
    assert relations["tiny"].row_count == 3
    assert relations["large"].row_count == 100_000
    columns = {c.name: c for c in relations["tiny"].columns}
    assert columns["happened_on"].minimum == "2031-04-02"
    assert columns["happened_on"].maximum == "2031-04-09"
    assert columns["happened_on"].null_count == 1
    assert columns["loaded_at"].maximum == "2031-04-15T00:00:00"
    assert columns["amount"].null_count == 0
    assert columns["happened_on"].nullable is True
    assert relations["labels"].temporal_status == "no_temporal_columns"
    assert all("JOIN" not in q.upper() for q in queries)
    aggregates = [q for q in queries if q.startswith("SELECT")]
    assert len(aggregates) == 3
    assert all(sum(f'"{name}"' in q for name in relations) == 1 for q in aggregates)
    assert "completeness" in result.coverage_caveat
    assert "defect" not in result.model_dump_json().lower()
    event = profile_service.ledger.tool_events[-1]
    assert event.output == result.model_dump(mode="json")
    assert event.output["result_contract_version"] == "1.0"
    assert profile_service.ledger.budget.sql_executions == 1


def test_explicit_temporal_selection_and_source_lag(profile_service):
    from tools.sql import RelationProfileRequest, SourceLagRequest

    result = profile_service.inspect_relations(
        profiles=[
            RelationProfileRequest(relation_name=name, temporal_columns=["happened_on"])
            for name in ("tiny", "large")
        ],
        source_lags=[
            SourceLagRequest(
                relation_name="tiny",
                column_name="happened_on",
                reference_relation="large",
                reference_column="happened_on",
            )
        ],
    )
    assert result.source_lags[0].lag_seconds == 3 * 86400
    assert not result.relations[0].columns[1].temporal_profiled
    assert result.relations[0].columns[1].maximum is None
    with pytest.raises(ValueError, match="temporal"):
        profile_service.inspect_relations(
            profiles=[
                RelationProfileRequest(
                    relation_name="tiny", temporal_columns=["amount"]
                )
            ]
        )
    assert profile_service.ledger.tool_events[-1].status.value == "failed"


@pytest.mark.parametrize("role", [AgentRole.ANALYST, AgentRole.GENERALIST])
def test_profile_adapter_and_inspection_without_recomputation(tmp_path, role):
    context = _context(tmp_path, role)
    response = _invoke(inspect_relations, context, {})
    assert response.success
    event_id = response.data["tool_event_id"]
    before = context.ledger.budget.sql_executions
    inspected = _invoke(inspect_evidence, context, {"reference": event_id})
    assert inspected.success and inspected.data["result_available"]
    assert inspected.data["output"] == response.data
    assert context.ledger.budget.sql_executions == before


def test_native_timeout_stops_computation_closes_connection_and_rejects_binding(
    tmp_path, monkeypatch
):
    context = _context(tmp_path)
    service = DuckDBExecutionService(
        context.workspace, context.ledger, sql_timeout_seconds=0.1
    )
    native_errors, connections, closed = [], [], []
    original = duckdb.connect

    class ObservedConnection:
        def __init__(self):
            self.inner = original()
            connections.append(self.inner)

        def execute(self, sql, *args, **kwargs):
            try:
                return self.inner.execute(sql, *args, **kwargs)
            except duckdb.InterruptException as exc:
                native_errors.append(exc)
                raise

        def close(self):
            self.inner.close()
            closed.append(self)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(duckdb, "connect", lambda **kwargs: ObservedConnection())
    before_threads = set(threading.enumerate())
    started = time.monotonic()
    result = service.execute(EXPENSIVE_SQL, query_id="expensive")
    assert time.monotonic() - started < 3
    assert native_errors, "must observe native Interrupted, not just abandon an await"
    assert closed and not result.success and result.timed_out
    assert not result.rows
    assert set(threading.enumerate()) == before_threads
    with pytest.raises(duckdb.ConnectionException):
        connections[0].execute("SELECT 1")
    ledger = AnalysisLedger(context.workspace)
    assert ledger.budget.sql_executions == 1
    assert len(ledger.tool_events) == 1
    event = ledger.tool_events[0]
    assert event.status.value == "failed" and event.output is None
    assert event.arguments["sql_timeout_seconds"] == 0.1
    assert event.id not in executed_references(ledger)
    assert "expensive" not in executed_references(ledger)
    claim = MetricComparison(
        metric_key="sum",
        baseline_period="a",
        comparison_period="b",
        comparison_type="absolute_difference",
        unit="currency",
        evidence_refs=[event.id],
        computation=ComputationBinding(
            tool_event_id=event.id,
            source="sql_rows",
            fields=[ComputedField(field="value", pointer="/0/0")],
        ),
    )
    with pytest.raises(ResultBindingError, match="successful retained"):
        resolve_result(claim, ledger)
    inspected = _invoke(inspect_evidence, context, {"reference": event.id})
    assert not inspected.data["result_available"]
    assert not inspected.data["provenance_verified"]
    snapshot = context.ledger.state_path.read_bytes()
    time.sleep(0.2)
    assert context.ledger.state_path.read_bytes() == snapshot
    # Small cross products are valid and still share the P0.1 capture path.
    good = service.execute(
        "SELECT a.i + b.j FROM range(2) a(i) CROSS JOIN range(2) b(j) ORDER BY 1"
    )
    assert good.success and good.rows == [[0], [1], [1], [2]]
    assert context.ledger.budget.sql_executions == 2
    assert len(context.ledger.tool_events) == 2
    inspected = _invoke(inspect_evidence, context, {"reference": good.tool_event_id})
    assert inspected.data["output"]["rows"] == good.rows
    assert len(closed) == 2


def test_timeout_from_sdk_style_worker_exits_without_forced_process_shutdown(tmp_path):
    script = """
import asyncio, threading
from tools.workspace import WorkspaceManager
from tools.sql import DuckDBExecutionService
from orchestration.ledger import AnalysisLedger
async def main():
    workspace = WorkspaceManager(ROOT).create_workspace("worker")
    ledger = AnalysisLedger(workspace, objective="cancel")
    service = DuckDBExecutionService(workspace, ledger, sql_timeout_seconds=.1)
    result = await asyncio.to_thread(service.execute, SQL)
    assert result.timed_out and not result.success
    assert len(ledger.tool_events) == 1
asyncio.run(main())
assert len(threading.enumerate()) == 1
print("clean normal exit")
"""
    script = f"ROOT={str(tmp_path)!r}\nSQL={EXPENSIVE_SQL!r}\n" + script
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=8,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert result.returncode == 0, result.stderr
    assert "clean normal exit" in result.stdout


@pytest.mark.parametrize(
    "invalid", [0, -0.1, 0.001, 121, True, float("nan"), float("inf"), "1"]
)
def test_sql_timeout_is_explicit_and_validated(tmp_path, invalid):
    workspace = WorkspaceManager(tmp_path).create_workspace("invalid")
    from agents.runtime import AgentRunConfig
    from orchestration.runner import AnalysisRunner

    with pytest.raises(ValueError, match="sql_timeout_seconds"):
        DuckDBExecutionService(workspace, sql_timeout_seconds=invalid)
    with pytest.raises(ValueError, match="sql_timeout_seconds"):
        AgentRunConfig(
            run_id="invalid", agent_role=AgentRole.ANALYST, sql_timeout_seconds=invalid
        )
    with pytest.raises(ValueError, match="sql_timeout_seconds"):
        AnalysisRunner(sql_timeout_seconds=invalid)


def test_future_manifest_fingerprints_sql_timeout(tmp_path):
    runner = BenchmarkRunner(tmp_path)
    common = dict(manifest_id="p03-test-only", model="fixture", repetitions=3)
    first = runner.build_manifest(**common, sql_timeout_seconds=10)
    second = runner.build_manifest(**common, sql_timeout_seconds=20)
    assert first.run_configuration.parameters["sql_timeout_seconds"] == 10
    assert canonical_manifest_declaration_digest(
        first
    ) != canonical_manifest_declaration_digest(second)
    assert first.run_configuration.tool_contract_version != "1.0"


def test_outer_tool_cancellation_drains_native_worker_before_return(
    tmp_path, monkeypatch
):
    import asyncio
    import json

    from agents.tool_context import ToolContext

    from agents import run_sql

    context = _context(tmp_path)
    entered = threading.Event()
    original = duckdb.connect
    interruptions = []

    class Connection:
        def __init__(self):
            self.inner = original()

        def execute(self, sql, *args, **kwargs):
            if sql == EXPENSIVE_SQL:
                entered.set()
            try:
                return self.inner.execute(sql, *args, **kwargs)
            except duckdb.InterruptException:
                interruptions.append(True)
                raise

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(duckdb, "connect", lambda **kwargs: Connection())

    async def exercise():
        payload = json.dumps({"sql": EXPENSIVE_SQL})
        wrapper = ToolContext(
            context, tool_name="run_sql", tool_call_id="cancel", tool_arguments=payload
        )
        task = asyncio.create_task(run_sql.on_invoke_tool(wrapper, payload))
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()
        started = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.monotonic() - started < 2
        assert interruptions
        assert len(context.ledger.tool_events) == 1
        assert context.ledger.tool_events[0].status.value == "failed"
        assert context.ledger.budget.sql_executions == 1
        snapshot = context.ledger.state_path.read_bytes()
        await asyncio.sleep(0.1)
        assert context.ledger.state_path.read_bytes() == snapshot

    asyncio.run(exercise())


def test_profile_timeout_has_one_inspectable_failure(tmp_path, monkeypatch):
    from tools.sql import SQLTimeoutError

    context = _context(tmp_path)
    context.sql_service.sql_timeout_seconds = 0.1
    original = duckdb.connect
    interrupted = []

    class Connection:
        def __init__(self):
            self.inner = original()

        def execute(self, sql, *args, **kwargs):
            # Run a bounded synthetic native query instead of scanning the
            # profile fixture; never execute any retained pathological SQL.
            if sql.startswith("SELECT COUNT(*)"):
                sql = EXPENSIVE_SQL
            try:
                return self.inner.execute(sql, *args, **kwargs)
            except duckdb.InterruptException:
                interrupted.append(True)
                raise

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(duckdb, "connect", lambda **kwargs: Connection())
    response = _invoke(inspect_relations, context)
    assert not response.success and response.data["timed_out"]
    assert SQLTimeoutError.__name__ in response.error.message
    assert interrupted
    assert len(context.ledger.tool_events) == 1
    event = context.ledger.tool_events[0]
    assert event.id == response.data["tool_event_id"]
    assert event.status.value == "failed" and event.output is None
    assert context.ledger.budget.sql_executions == 1
    assert event.id not in executed_references(context.ledger)
    inspected = _invoke(inspect_evidence, context, {"reference": event.id})
    assert not inspected.data["result_available"]


def test_deadline_between_statements_cannot_publish_late_success(tmp_path, monkeypatch):
    context = _context(tmp_path)
    service = DuckDBExecutionService(
        context.workspace, context.ledger, sql_timeout_seconds=0.05
    )
    original = service._register_input_views

    def paused_setup(connection):
        original(connection)
        # The first interrupt hits an idle connection. A later quick SELECT
        # may complete, but it must never be returned/persisted as success.
        time.sleep(0.1)

    monkeypatch.setattr(service, "_register_input_views", paused_setup)
    result = service.execute("SELECT 123")
    assert result.timed_out and not result.success and result.rows == []
    assert context.ledger.tool_events[0].output is None
    assert context.ledger.budget.sql_executions == 1


def test_empty_all_null_and_non_temporal_profiles(tmp_path):
    from tools.sql import SourceLagRequest

    source = tmp_path / "source"
    source.mkdir()
    for name, values in [("empty", []), ("nulls", [None, None])]:
        pq.write_table(
            pa.table({'odd " time': pa.array(values, type=pa.timestamp("us"))}),
            source / f"{name}.parquet",
        )
    pq.write_table(pa.table({"date": ["2031-01-01"]}), source / "text.parquet")
    workspace = WorkspaceManager(tmp_path / "runs").create_workspace(
        "edge", inputs_source=source
    )
    service = DuckDBExecutionService(workspace)
    result = service.inspect_relations(
        source_lags=[
            SourceLagRequest(
                relation_name="empty",
                column_name='odd " time',
                reference_relation="nulls",
                reference_column='odd " time',
            )
        ]
    )
    assert result.source_lags[0].lag_seconds is None
    empty, nulls, text = result.relations
    assert empty.row_count == 0 and empty.columns[0].null_count == 0
    assert nulls.row_count == 2 and nulls.columns[0].null_count == 2
    assert empty.columns[0].minimum is None and nulls.columns[0].maximum is None
    assert text.temporal_status == "no_temporal_columns"
    assert not text.columns[0].temporal_profiled


def test_lag_requires_matching_selected_columns(profile_service):
    from tools.sql import RelationProfileRequest, SourceLagRequest

    with pytest.raises(ValueError, match="matching temporal types"):
        profile_service.inspect_relations(
            source_lags=[
                SourceLagRequest(
                    relation_name="tiny",
                    column_name="happened_on",
                    reference_relation="large",
                    reference_column="loaded_at",
                )
            ]
        )
    with pytest.raises(ValueError, match="explicitly profiled"):
        profile_service.inspect_relations(
            profiles=[
                RelationProfileRequest(relation_name="tiny", temporal_columns=[])
            ],
            source_lags=[
                SourceLagRequest(
                    relation_name="tiny",
                    column_name="happened_on",
                    reference_relation="large",
                    reference_column="happened_on",
                )
            ],
        )


@pytest.mark.parametrize("role", [AgentRole.DATA_AUDITOR, AgentRole.GENERALIST])
def test_runners_wire_the_same_sql_bound(tmp_path, role):
    from orchestration.generalist_runner import GeneralistRunner
    from orchestration.runner import AnalysisRunner

    runner_type = GeneralistRunner if role is AgentRole.GENERALIST else AnalysisRunner
    runner = runner_type(workspace_base_dir=tmp_path, sql_timeout_seconds=0.1)
    workspace = runner.workspace_manager.create_workspace("bound")
    ledger = AnalysisLedger(workspace, objective="test")
    context, _ = runner._agent_context(workspace, ledger, role)
    assert context.run_config.sql_timeout_seconds == 0.1
    result = context.sql_service.execute(EXPENSIVE_SQL)
    assert result.timed_out and not result.success
    assert ledger.budget.sql_executions == 1


def test_future_live_manifest_requires_declared_sql_contract(tmp_path):
    import json

    from benchmark import BenchmarkError
    from evaluation.contracts import ExecutionMode

    runner = BenchmarkRunner(tmp_path)
    manifest = runner.build_manifest(
        manifest_id="missing-bound", model="fixture", execution_mode=ExecutionMode.LIVE
    )
    path = tmp_path / "manifest.json"
    payload = manifest.model_dump(mode="json")
    del payload["run_configuration"]["parameters"]["sql_timeout_seconds"]
    path.write_text(json.dumps(payload))
    with pytest.raises(BenchmarkError, match="sql_timeout_seconds"):
        runner.execute(path, require_pilot=False)
    with pytest.raises(BenchmarkError, match="sql_timeout_seconds"):
        BenchmarkRunner(tmp_path, runner_options={"sql_timeout_seconds": 1})


def test_event_loop_shutdown_cancels_and_drains_sql_tool(tmp_path):
    script = """
import asyncio, json, threading
from pathlib import Path
import duckdb
from agents import run_sql
from agents.tool_context import ToolContext
from tests.test_result_binding import _context
context = _context(Path(ROOT))
entered = threading.Event()
original = duckdb.connect
class Connection:
    def __init__(self):
        self.inner = original()
    def execute(self, sql, *args, **kwargs):
        if sql == SQL:
            entered.set()
        return self.inner.execute(sql, *args, **kwargs)
    def __getattr__(self, name):
        return getattr(self.inner, name)
duckdb.connect = lambda **kwargs: Connection()
async def main():
    payload = json.dumps({"sql": SQL})
    wrapper = ToolContext(
        context, tool_name="run_sql", tool_call_id="shutdown", tool_arguments=payload
    )
    task = asyncio.create_task(run_sql.on_invoke_tool(wrapper, payload))
    while not entered.is_set():
        await asyncio.sleep(.01)
    assert not task.done()
    # Normal asyncio.run shutdown cancels every outstanding Task.
asyncio.run(main())
assert len(threading.enumerate()) == 1
assert len(context.ledger.tool_events) == 1
assert context.ledger.tool_events[0].status.value == "failed"
assert "SQLCancelledError" in context.ledger.tool_events[0].error
print("clean cancelled shutdown")
"""
    script = f"ROOT={str(tmp_path)!r}\nSQL={EXPENSIVE_SQL!r}\n" + script
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=8,
        env={**os.environ, "PYTHONPATH": "src:."},
    )
    assert result.returncode == 0, result.stderr
    assert "clean cancelled shutdown" in result.stdout


def test_simultaneous_queries_have_independent_cancellation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    context = _context(tmp_path)
    service = DuckDBExecutionService(
        context.workspace, context.ledger, sql_timeout_seconds=0.2
    )
    before_threads = set(threading.enumerate())
    with ThreadPoolExecutor(max_workers=2) as workers:
        slow = workers.submit(service.execute, EXPENSIVE_SQL, query_id="slow")
        fast = workers.submit(service.execute, "SELECT 42", query_id="fast")
        assert fast.result(timeout=3).rows == [[42]]
        assert slow.result(timeout=3).timed_out
    assert set(threading.enumerate()) == before_threads
    assert context.ledger.budget.sql_executions == 2
    assert len(context.ledger.tool_events) == 2
    assert {e.status.value for e in context.ledger.tool_events} == {
        "succeeded",
        "failed",
    }
