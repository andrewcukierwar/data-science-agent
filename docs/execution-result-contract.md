# P0.1: persisted execution results

The SQL and Python services now return a canonical `tool_event_id` for each
recorded execution. Copy it verbatim; do not construct IDs from query or script
names. IDs are unique across languages and executions. `attempt_id` is retained
when a run has an active attempt; it is null for executions outside an attempt.
Services without a ledger return no citable event ID.

Successful calculation payloads carry `result_contract_version: "1.0"` in
`ToolEvent.output` and tool responses. This is the first versioned calculation
payload; earlier outputs were unversioned. The enclosing workspace schema stays
at 1.1: its existing arbitrary output dictionary accommodates these fields.
No evaluator schema, scoring rule, benchmark task, or frozen workspace changes.
A future benchmark must declare the changed code/tool configuration separately.

## SQL output

The successful event retains query ID/path, execution/attempt identity, columns,
column types, and whole result rows. The service returns the same normalized rows
that it persists. Capture is bounded by the existing `max_rows` and a new
1,000,000-byte limit measured using compact ASCII JSON, including result
metadata. Whole rows are omitted when they do not fit; cell values are never
silently shortened. Oversized column metadata fails capture with a failed event
rather than saving an unbounded or misleading successful result.

- `row_count`: fetched rows, capped at `max_rows`; this preserves its old meaning.
- `row_count_is_lower_bound`: more rows existed beyond the fetch limit. This
  means `row_count` is **not** the population size.
- `retained_row_count`: rows actually retained after both limits.
- `truncated`: either capture limit omitted rows, with `truncation_message`.
- `max_rows` and `max_result_bytes`: capture limits.
- `model_rows_truncated`: the initial tool response omitted additional retained
  rows due to the existing model row/text limits. Inspect the event for them.

JSON nulls, booleans, integers and finite floats retain their JSON types. Decimal
values use exact strings, temporal values use ISO strings, and UUIDs use strings.
Bytes use `{type: "bytes", hex: ...}`; non-finite floats use
`{type: "float", value: "nan" | "inf" | "-inf"}`. Intervals fetched as Python
`timedelta` use days/seconds/microseconds. Nested sequences and structures are
normalized recursively; non-string-keyed maps use sorted tagged entries.
`column_types` preserves DuckDB type information. Unsupported value conversions
fail capture rather than saving arbitrary object representations.

This bounds retained output, not SQL execution cost or fetch-time memory.
Cancellation and execution-resource controls belong to P0.3.

## Python output

The service and persisted event share bounded stdout/stderr and explicit
`stdout_truncated`/`stderr_truncated` flags. The existing default remains 4,000
characters per stream. The adapter can return a smaller preview and records
`model_stdout_truncated`/`model_stderr_truncated`; inspection retrieves the retained
streams. Both the service and adapter now report capture truncation.
Generated-file paths, hashes, sizes and change metadata remain in the event.
File contents remain separate current workspace artifacts; this change does not
snapshot every generated file or claim that a later file is its historical
content. To preserve a calculation directly in the execution result, emit it on
stdout within the capture limit. Reused generated-file aliases require an event
ID; that event retains its own streams and original file metadata.

## Inspection and references

Every role has read-only `inspect_evidence`; Lead still cannot execute SQL or
Python. The multi-agent and generalist architectures use the same implementation
and unchanged computation/model budgets.

`inspect_evidence(reference)` defaults to the recorded execution result for an
exact event ID or an unambiguous query/script ID, query/script path, generated
file reference, or associated artifact ID/path. Finding aliases can resolve to
one execution if all their dependencies resolve. A finding cannot hide a failed
or ambiguous dependency behind a successful one. An alias shared by multiple
executions is rejected even if only one succeeded or the executions occurred in
different attempts. Exact event IDs disambiguate; neither recency nor file
modification time selects an execution. Provenance uses the same alias index and
rejects these ambiguous references too. Existing lineage checks still apply:
inspectability alone does not validate a numerical claim or unsupported constants.

`inspect_evidence(reference, view="source")` reads current file contents using a
workspace path or artifact ID. For SQL/Python paths this returns source code,
separately from executed output. Unexecuted files remain inspectable as files.
Existing path restrictions and artifact verification remain in force.

Large event outputs return bounded JSON text pages under `output.preview_json`.
Use the returned canonical event ID and pass `output.next_offset` as the next
`output_offset` until it is null. Concatenating the pages reconstructs exactly
the retained JSON. Page `truncated` describes transport; `result_truncated`
describes omitted calculation output. Paging cannot recover omitted rows or
stream characters and does not turn a sample into a complete population.

Failed events remain inspectable as failure diagnostics (`status: "failed"`,
`provenance_verified: false`, `result_available: false`). Their aliases cannot
establish successful evidence. Registration cannot turn a failed execution into
successful evidence.

## Compatibility and scope

Old ledgers load without migration, rewriting, or recomputation. Existing event
IDs still work; aliases use the same unambiguous lookup. Legacy SQL events without
rows explicitly report `result_available: false`; the missing values cannot be
recovered from saved source alone. New callers should consume additive fields,
use returned event IDs, and request `view="source"` for file contents. Direct SQL
service consumers now receive JSON-normalized values; direct Python consumers
receive bounded streams rather than unbounded streams.

P0.2 remains deferred: binding selected metric/statistical fields to computation,
preventing transcription errors, and carrying selected statistics and limitations
through Critic, synthesis, and reporting. No analytical operators, retries,
evaluator changes, scenario mappings, UI, AWS, or paid model calls are included.
