# P1.1b: analytical agent integration contract

This contract exposes the P1.1a deterministic primitives to the agent runtime.
It defines one typed analytical tool, role boundaries, source acquisition, and
the path from retained computation evidence to selected numerical claims. It
does not change the arithmetic contract in
[analytical-primitives-contract.md](analytical-primitives-contract.md), the
P0.2 binding contract, or the existing SQL/Python services.

## One typed tool

`run_analytical` is the only analytical tool. Its request is the Pydantic
`AnalyticalRequest`, a discriminated union whose `operation` field selects
exactly one of:

- `coverage`
- `entity_aggregate`
- `ratio`
- `contrast`
- `reconciliation`
- `binary_experiment`

The adapter validates the request, applies the operation-level role guard, and
calls `AnalyticalExecutionService` with the current run ledger. It performs no
hidden source query, SQL rewrite, sampling, retry, or model call. A successful
response includes the canonical `tool_event_id`, a distinct
`analytical_record_id`, the typed retained record, and binding pointers for
published quantity values. The record's scopes, quantities, warnings, and
details are inspectable through the existing evidence path.

Request fields must state the applicable population, grain, date or cohort
field, period or observation window, dimensions, measures, aggregation,
expectations, tolerances, or experiment design. The operation's structural
checks protect declared identities and arithmetic choices; they do not decide
whether the business population or estimand is appropriate.

## Role permissions

The runtime enforces both visibility of `run_analytical` and the operation
discriminator behind it:

| Role | Permitted analytical operations | Boundary |
| --- | --- | --- |
| Lead | None | Coordinates the investigation, reuses suitable mandatory-audit sources, or arranges Analyst source work. It remains unable to execute SQL, Python, or analytical computation. |
| Data Auditor | `coverage` | Checks explicit expected date or dimension grids. |
| Analyst | `entity_aggregate`, `ratio`, `contrast`, `reconciliation` | Performs entity, ratio, comparison, and reconciliation calculations. |
| Statistician | `binary_experiment` | Has no SQL permission; use Python for other inferential work. |
| Generalist | The union of all operations above | Retains the single-agent capability union without specialist delegation. |
| Critic | None | Existing Critic tools and validation behavior remain unchanged; it may inspect persisted evidence but cannot execute `run_analytical`. |

Specialists do not delegate to other specialists. Lead and Generalist
finalization use the existing shared result path; this contract does not add a
second specialist interface.

## Source acquisition and completeness

Before selecting an operation, an agent reads the relevant business definitions
and inspects relation schemas and date bases. It uses narrow SQL projections
and legitimate filters to produce a complete retained source with the exact
`tool_event_id` returned by `run_sql`. Source operations accept only successful,
canonical, complete, untruncated SQL results. Derived operations accept
validated `run_analytical` event IDs and retained quantity names.

Supported role-approved primitives are the default calculation path when a
complete, valid source is available. For binary experiments, Lead reuses a
suitable mandatory-audit SQL source or commissions Analyst source construction
first, then passes the exact successful SQL `tool_event_id` to Statistician.
Statistician inspects that event; it cannot execute SQL or delegate source work.

An agent must not treat a model-preview truncation, SQL row cap, result-byte cap,
or sample as a complete population. If a source exceeds a capture cap, the
agent may narrow the projection or apply legitimate population-preserving
filters, then execute the resulting complete query. It must not sample, split
and silently recombine hidden partitions, or change the requested population.
If no complete retained source can be produced, the agent reports the
limitation and uses a bounded SQL or Python calculation only when its
provenance and population remain explicit. Unsupported calculations continue
to use the existing SQL or Python tools; raw SQL and Python remain available
for source construction, exploration, and methods outside these primitives.

Entity counts must come from entity-first aggregation rather than post-join row
counts. Sparse event dates do not establish a reporting gap without an
explicit expected grid or cadence. No source availability or completeness is
inferred by the analytical adapter.

## Computation evidence and claim finalization

The identities are deliberately separate:

- `tool_event_id` identifies the retained `run_analytical` execution and is the
  canonical evidence reference.
- `analytical_record_id` identifies the content-derived analytical record. It
  is evidence, not a specialist claim `result_id`, and must not be placed in
  `selected_result_ids`.
- A persisted MetricComparison, StatisticalAssessment, or numerical Finding
  receives its own application-assigned claim `result_id` after binding and
  finalization.

Numerical agent output follows P0.2: numerical fields and claim `result_id`
are initially null, while the model supplies the exact event ID and one
binding for every numerical field. An analytical binding uses source
`analytical_record` and a published pointer of the form
`/results/{index}/quantities/{name}/value`. Null or non-published quantities
cannot establish a numerical claim. Persistence revalidates the analytical
record identity, source digests, successful status, pointer, and exact value;
it does not copy a model-authored number or add a comparison tolerance.

The application hydrates the bound claim and assigns its separate claim ID.
Lead selects persisted claim IDs, retaining their caveats and evidence; the
Generalist uses the same finalizer. Critic validation rechecks binding,
provenance, and conflicts before review. Existing evidence inspection reads a
retained analytical record by exact event ID without recomputation or another
computation-budget charge.

## Budgets, parity, and limits

The analytical service reserves one existing Python computation budget slot per
execution. Source SQL remains charged by the existing SQL service. Model,
turn, specialist-invocation, Critic-loop, SQL, and chart budgets are unchanged.
The Generalist receives the same deterministic operation union exposed by the
specialist permissions; it does not gain a separate implementation or hidden
budget.

This integration does not claim that agents have adopted or selected these
operations in a live run. Deterministic acceptance verification is complete.

### Acceptance results (2026-09-22)

Independent Sol High review covered `3a644a85..0231f589` and the necessary
contracts. It found no Critical or High issues and one Medium issue: older
SQL/Python-only instructions conflicted with primitive adoption, and Lead lacked
explicit SQL-source handoff guidance for Statistician. The accepted Luna High
fix aligned role instructions and delegation descriptions, added a guidance
regression, and corrected Ruff formatting. No P1.1a arithmetic was changed.

- Final focused integration, analytical, P0.1/P0.2/P0.3, and role/tool/binding
  regression command: **297 passed**, including all **14 P1.1b integration**
  and **41 P1.1a analytical** cases.
- Full `uv run pytest -m 'not live' -q`: **820 passed, 3 skipped,
  17 deselected**. Docker-dependent tests skipped because the local Docker
  socket is unavailable. One existing negative-test Pydantic serialization
  warning remains.
- `uv run ruff check .`, `uv run ruff format --check .` (**193 files**), and
  `git diff --check`: passed.

Commands used `UV_CACHE_DIR=/private/tmp/p11b-uv-cache` because the default cache
is outside the writable sandbox. No live/model/paid calls were made.

The integration fixtures use arbitrary names and shifted 2043 dates. They reject
failed, truncated, and ambiguous sources and verify exact bound values through
specialist persistence, Lead selection, Critic candidate validation, final report,
and offline evaluator without model retyping. The Generalist exposes exactly the
specialist operation union and uses the same binding/finalization machinery.
Large responses retain canonical IDs and an explicit pageable inspection route.
The reviewed production additions contain no scenario IDs, expected answers,
hidden causes, or evaluator-only information.

Lead remains non-computational. Critic has no `run_analytical` capability; its
pre-existing SQL/Python verification permissions remain unchanged.

Autonomous primitive selection, Analyst-to-Statistician source sequencing,
complete paged inspection, and correct model-authored binding selection remain
unverified without live calls. Deterministic plumbing and prompt regressions do
not establish those behaviors. Subject to those stated limitations, P1.1b is
ready to close; P1.2 has not begun.

P1.2 Critic policy and repair changes remain deferred. Large-result streaming,
additional cadences, arbitrary arithmetic expressions, automatic statistical
test selection, predictive models, and causal models remain outside this
contract.
