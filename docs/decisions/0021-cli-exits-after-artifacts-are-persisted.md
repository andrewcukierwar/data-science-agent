# 0021: The benchmark CLI exits once artifacts are persisted

- Status: Accepted
- Date: 2026-08-21
- Phase: Phase 2 / Task 10

## Context

The 60-cell matrix finished, persisted every manifest, report, and workspace,
and printed its complete summary — then never exited. The process stayed alive
for four hours consuming roughly 12.7 CPU-hours.

A stack sample showed the main thread parked in
`_PyErr_PrintEx -> Py_Exit -> Py_FinalizeEx -> wait_for_thread_shutdown`, the
normal `SystemExit` path blocked while joining non-daemon threads. Four other
threads were still inside `DuckDBPyConnection::Execute`, and 43 were spinning in
`duckdb::TaskScheduler::ExecuteForever`.

The mechanism is the interaction between two correct components. The Agents SDK
runs synchronous function tools on worker threads. When the frozen
`agent_run_timeout_seconds` bound from decision 0018 fires, asyncio cancels the
await, but it cannot cancel a thread: an in-flight DuckDB query keeps running to
completion on an uncancellable worker. `DuckDBExecutionService` already closes
every connection in a `finally`, and a standalone connect/execute/close loop
exits promptly, so this is not a leaked connection.

Five matrix cells hit the timeout bound, which is consistent with the orphaned
threads observed at shutdown.

## Decision

- After `main()` returns, flush the standard streams and terminate with
  `os._exit`, preserving the exact exit code.
- Do not wait on threads the runtime cannot cancel. Every manifest, report, and
  workspace is written before the CLI reaches this point, so joining orphaned
  tool threads only hangs the command.

## Consequences

The command terminates promptly with its real exit code, so automation and CI
cannot hang on a completed run. Benchmark evidence is unaffected: this changes
only process teardown, after all persistence.

This mitigates the symptom, not the underlying uncancellable tool thread. A
timed-out cell still leaves a query running until DuckDB finishes it, and its
usage remains incomplete with cost unavailable, exactly as decision 0018
specifies. Cooperative interruption of an in-flight query — DuckDB exposes
`interrupt()` — remains open.

## Verification

The `report`, `dry-run`, and error paths still return 0, 0, and 2. The
deterministic suite passes 695 tests including the three Docker-backed
integrations, and Ruff passes across 168 files. The evidence for the hang is
retained in the run log and stack sample described above.
