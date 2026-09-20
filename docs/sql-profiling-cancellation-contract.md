# P0.3: independent source profiles and cancellable SQL

P0.3 extends the existing `inspect_relations` boundary and bounds both it and
`run_sql`. It does not add an overlapping tool, analytical operators, retries,
SQL rewriting, or a CROSS JOIN prohibition. Both architectures use the same
services and adapters. Lead retains its existing restriction on executing SQL.

## Profiling API (profile contract 1.0)

`inspect_relations(profiles=None, include_missingness=False, source_lags=None)`
returns relation row counts, columns/types, DuckDB schema nullability, and
observed min/max for **every** DATE/TIMESTAMP column by default. Column names do
not select date semantics. Strings, integer epochs, and time-only columns are
not guessed or converted. The caller can select relations and temporal columns:

```json
{
  "profiles": [
    {"relation_name": "activity", "temporal_columns": ["occurred_at"]},
    {"relation_name": "deliveries", "temporal_columns": ["received_at"]}
  ],
  "include_missingness": true,
  "source_lags": [{
    "relation_name": "activity",
    "column_name": "occurred_at",
    "reference_relation": "deliveries",
    "reference_column": "received_at"
  }]
}
```

`profiles=null` discovers all registered relations. An explicit list selects a
subset. Within each request, `temporal_columns=null` discovers all temporal
columns; `[]` omits temporal bounds. Unknown relations/columns, non-temporal
selections and duplicate relations fail clearly. Identifiers are validated
against the registered schema and quoted; input paths are not accepted.

Each relation has **one independent aggregate query** for the requested counts
and bounds. A call costs one existing SQL budget unit, regardless of the number
of relations. There is no join or Cartesian product between source rows.
Counts are population counts, unlike the fetched `row_count` in a SQL result.
Null counts are always included for profiled temporal columns and optionally
for every visible column. `nullable` is DuckDB schema metadata (views can be
conservatively nullable); observed `null_count` is a separate measurement.
The direct service retains `include_row_counts=False` for metadata consumers.

Each column has `temporal_profiled`, `minimum`, `maximum`, and `null_count`.
Bounds use the P0.1 ISO normalization. Multiple dates remain separate; the tool
never chooses an authoritative reporting date. Empty and all-null temporal
columns return null bounds, distinguished by relation/null counts. Relations
without temporal columns say `no_temporal_columns`. Explicit omissions say
`not_requested`. A truncated schema says `schema_truncated`, never falsely
claiming that unseen columns are non-temporal.

Source lag is **reference maximum minus source maximum**, in seconds. Positive
means the selected source's observed maximum is earlier. Columns must have the
same DuckDB temporal type and must have been profiled. The implementation
subtracts the independent scalar summaries in Python, without SQL joins.
Missing maxima produce null lag. No implicit DATE/TIMESTAMP conversion,
time-zone alignment, authoritative source selection, reporting calendar,
completeness judgment, or causal inference is made. Sparse events are not
classified as reporting defects. Expected-cadence/grid analytical operations
remain P1.1 work.

The existing limits remain 100 relations and 256 columns per relation. Explicit
oversized requests fail; automatic discovery flags omitted schemas. Profile
output is capped at the same 1,000,000-byte retained JSON ceiling as SQL. An
oversized profile fails rather than saving a misleading partial successful
profile; callers can select a smaller subset. Successful profile payloads carry
execution result contract `1.0`, profile contract `1.0`, attempt identity, and a
canonical event ID. `inspect_evidence` retrieves/paginates the retained output
without computation. Failed profile responses also return their diagnostic
event ID. No ledger means no advertised citable event ID.

## Native cancellation and lifecycle

The installed Agents SDK executes synchronous tools using `asyncio.to_thread`.
Cancelling its await cannot stop the thread. DuckDB's documented
[`DuckDBPyConnection.interrupt()`](https://duckdb.org/docs/current/clients/python/reference/#duckdb.DuckDBPyConnection.interrupt)
interrupts pending native work on that connection. The installed DuckDB 1.5.5
was probed before implementation: a `sum(sin(i))` over a huge generated range
raised native `InterruptException` after approximately 0.21 seconds with a
0.2-second interrupt, then closed and exited normally.

`tools/sql_deadline.py` owns a fresh in-memory connection per operation and a
daemon watchdog. The calling worker executes setup, queries and fetches; the
watchdog only signals `interrupt()`. It never changes results, budget, events,
or files. The deadline is monotonic and starts before opening the connection.
A profile call shares one deadline across all its relations.

The watchdog repeats interruption every 10 ms after expiry. A single interrupt
while the connection is idle can be cleared by a subsequent statement; repeated
signalling covers setup/query/fetch boundaries. The owner also checks expiry
before returning, rejecting a result that completed after the bound even if
DuckDB did not raise. It signals completion, joins the watchdog, and closes the
connection **before** publishing the single terminal event. No query future is
abandoned. No helper remains to turn a timeout into a later success.

The two SQL-backed SDK adapters are asynchronous wrappers that shield their
worker, forward enclosing cancellation through a per-invocation event, and
drain the worker before re-raising cancellation. Repeated cancellation does not
abandon that drain. This closes the additional race where the agent invocation
expires before the SQL deadline and finalizes while a tool is still working.
Cancellation during result normalization is also checked before publication.
A completion already published before cancellation remains a completion.

A timeout returns `success=false`, `timed_out=true`, no rows, and a
`SQLTimeoutError` diagnostic. Enclosing cancellation records `SQLCancelledError`
(and `cancelled=true` on direct SQL results). Both persist exactly one FAILED
terminal event with no successful output. The event retains the SQL artifact,
execution/attempt identity and configured bound. Budget is reserved once and
not refunded or charged again on interruption. Existing evidence resolution,
inspection and P0.2 binding reject these failed executions. Successful SQL
retains the unchanged P0.1 rows and binding path; profile metadata does not add
a new P0.2 numeric binding source or bypass its source-type checks.

This is **cooperative native cancellation**, not an OS hard-kill guarantee or a
memory quota. There is no extra query process. Tests establish actual native
interruption, connection closure, worker completion, and normal interpreter
exit on macOS arm64/Python 3.12/DuckDB 1.5.5. They do not prove a maximum cleanup
latency for a future DuckDB bug, kernel/filesystem stall, or uninterruptible
extension. The approved boundary registers no Python UDFs and disables external
access after view setup. No evidence from the supported workload required
stronger process isolation. The tests are portable and should gate DuckDB
upgrades; only this local platform was exercised here. The default bound is a
SQL execution deadline, not a bound on artifact writes/ledger serialization.

Decision 0021's CLI forced-exit safeguard remains; new cleanup tests deliberately
use normal interpreter shutdown without it. Its historical evidence is intact.

## Configuration and future experiment identity

`sql_timeout_seconds` defaults to **30 seconds**, with finite numeric values in
**[0.05, 120]** accepted. Booleans, strings, NaN, infinities and out-of-range
values fail. This is separate from the unchanged 300-second agent invocation
bound. The audited independent profiles took roughly 0.005–0.020 seconds; the
existing deterministic workloads and new 100,000-row profile fixture complete
well within 30 seconds. The default leaves headroom for useful joins while
returning failure before a runaway query consumes a whole default invocation.
It is not a measured production SLA or a benchmark speedup claim.

The same validator is used by the service, `AgentRunConfig`, `AnalysisRunner`
and inherited `GeneralistRunner`, and manifest construction. Context creation
rejects a service/config mismatch. The benchmark declaration freezes the value,
passes it to both runners, and rejects hidden runner-option overrides. The
planning CLI exposes `--sql-timeout-seconds`. Changing the value changes the
existing canonical declaration digest and invalidates prior pilot identity.
Future live execution rejects missing/invalid SQL bounds or an old tool contract.

The future benchmark **tool configuration contract is 1.1** (previously 1.0).
The execution payload remains 1.0 with additive fields, profiles separately
identify their 1.0 schema, and the workspace/numerical-binding contracts remain
1.2/1.0. Existing code revision plus working-tree digest captures implementation
and guidance changes. Future experiments must use a new immutable manifest/run
identity with the new code/configuration; frozen v8 is neither upgraded nor
rewritten. No evaluator rules or expected values change.

## Regressions and scope

`tests/test_sql_reliability.py` was added before production changes: its first
run reported 13 expected failures and two existing-behavior passes. It covers
independent aggregates with differently sized sources, arbitrary and multiple
temporal columns, non-temporal/empty/all-null sources, sparse dates, explicit
lag semantics, nullability/missingness, successful inspection without reruns,
native timeout/outer cancellation, single events/budget charges, failed binding,
no late success, valid small CROSS JOIN, subsequent queries, connection/thread
cleanup, normal subprocess exit, validation, architecture parity, and manifest
fingerprints. The synthetic fixture reproduces the retained independent-date-
range failure mechanism without running any historical pathological query.

This addresses audit R6's implementation gap and identifies the enclosing-tool
cancellation race within the same mechanism. It changes no historical benchmark
finding and provides no evidence of future model adoption or analytical quality.
P1.1 analytical operators, P1.2 Critic/finalization policy, and P1.3/E1 task,
evaluator and scenario-ID boundaries remain deferred. No UI, AWS, predictive
ML, retries, new benchmark, paid calls, or retained-cell reruns are included.

## Implementation map and verification (2026-09-20)

Files changed:

- `src/tools/sql.py`: typed profile API, independent aggregates, scalar lags,
  bounded connections and failed-result identity.
- `src/tools/sql_deadline.py`: validated bound, native interruption and cleanup.
- `src/agents/tools.py`: shared async cancellation/drain and profile adapter.
- `src/agents/runtime.py`: validated configuration and service/config agreement.
- `src/orchestration/runner.py`: identical configuration for both architectures.
- `src/benchmark/runner.py`: frozen bound, declaration identity and contract gate.
- `scripts/run_benchmark.py`: explicit planning configuration flag.
- `skills/business_analytics.md`: use profiles for independent source metadata.
- `tests/test_sql_reliability.py`: 25 focused offline regressions.
- `docs/sql-profiling-cancellation-contract.md`: this API/lifecycle report.
- `docs/execution-result-contract.md`: link P0.1 capture to the P0.3 deadline.
- `docs/decisions/0022-independent-profiles-and-native-sql-cancellation.md` and
  `docs/decisions/README.md`: decision and index.

Final checks (all used `UV_CACHE_DIR=/tmp/data-science-agent-uv`):

- `uv run pytest tests/test_sql_reliability.py -q`: **25 passed**.
- SQL/tool/runtime/budget/ledger/runner/generalist/provenance/benchmark-config
  selection, including `test_execution_evidence.py` and `test_result_binding.py`:
  **251 passed**. Files: `test_sql_reliability.py`, `test_sql.py`,
  `test_agent_runtime.py`, `test_python.py`, `test_sandbox.py`, `test_ledger.py`,
  `test_runner.py`, `test_generalist.py`, `test_audit_provenance.py`,
  `test_citation_resolution.py`, `test_execution_evidence.py`,
  `test_result_binding.py`, `test_benchmark_runner.py`.
- `uv run pytest -m 'not live' -q`: **765 passed, 3 skipped, 17 deselected**.
  The three Docker integrations were collected and skipped because Docker's
  daemon socket was absent; `docker info` confirmed it was unavailable.
  One pre-existing Pydantic serialization warning in a negative evaluator test
  remains. Both full and focused pytest processes exited with status 0.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: passed (184 files).
- `git diff --check`: passed.

Cancellation tests observe native `InterruptException`, verify that the native
connection rejects use after closure, compare live thread sets, and assert one
persisted failure/budget charge. They check unchanged ledger bytes after waiting,
a subsequent successful query, concurrent connection isolation, and subprocess
exit under a test-enforced timeout. A separate subprocess lets normal
`asyncio.run` shutdown cancel an active SQL tool; it verifies failed terminal
persistence and no remaining threads without `os._exit`. The adapter retains
the executor Future rather than an intermediate Task, so global loop shutdown
cannot independently cancel that Task and detach it from its native computation.
No test launches a model or executes a retained pathological query.
