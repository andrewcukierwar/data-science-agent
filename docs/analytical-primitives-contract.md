# P1.1a: deterministic analytical primitives (contract 1.0)

Implemented 2026-09-22. This is a library computation boundary, not an agent tool
or a business metric catalog. `AnalyticalExecutionService(ledger).execute(request)`
accepts the typed requests in `schemas.analytical` and returns a retained
`AnalyticalRecord`. The caller chooses the business question, sources, entity
keys, population, period, dimensions, observation window, numerator/denominator,
units, hypotheses, and interpretation. Raw SQL and Python remain available.

## Inputs and scope

`TableInput(tool_event_id, relation)` references one successful canonical retained
SQL execution. `relation` records the caller's source role/name; the event and
its retained output identify the actual executed input. Rows are not supplied
by a model. Truncated, failed, timed-out, ambiguous-column, malformed, or
incomplete results are rejected. Each input is limited to 100,000 rows and the
existing SQL capture limits. This layer does not run hidden source queries.

Periods are half-open calendar intervals `[start, end)`. DATE columns are accepted
by default. Timestamp-to-date conversion requires an explicit `naive_date` or
`utc_date` policy. Entity observation windows are day offsets
`[cohort_date + start_day, cohort_date + end_day)`, bounded by the caller's
exclusive `observed_until` date. Source availability is never inferred.

Every result includes population, grain, period, dimensions, observation window,
operation semantics, method, quantities with units, and warnings. The record
also retains the full request, source event bindings and output digests, its
execution event ID, and a canonical content-derived result ID. Structural
checks validate declared choices; they cannot prove that a caller selected the
right business population or honestly described its semantics.

## Operations

### Expected-grid coverage

`CoverageRequest` names a source and temporal field. Optional expectations are
either explicit dates or an explicitly supplied daily cadence over the period.
A dimension grid additionally requires a dimension field and expected members.
The implementation counts actual observed date/member cells independently of
the generated expectation. It retains observation and distinct-cell counts,
observed cells, missing cells, and whole missing dates. It distinguishes a missing
day from one absent member on the latest day. With no cadence or expected dates,
completeness is unassessed, not false: sparse events are not reporting defects.
Expected grids are limited to 100,000 cells.

### Entity-first aggregation and independent ratios

`EntityAggregateRequest` requires a unique entity key and cohort date. Optional
events require their own unique key, entity key, date, and observation window.
Duplicate entity/event keys and orphan events fail; joined rows cannot quietly
multiply entity counts or event sums. Each entity's qualifying events are
collected before entity-level arithmetic. Dimension mappings select entity
attributes and produce separate scoped results, not an implicit mix of overall
and segment values.

Generic `Measure` kinds are `entity_count`, `entity_value`, `event_count`,
`event_sum`, and `event_at_least`. These supply counts, repeat/conversion
indicators, and post-cohort amounts without named business metrics. Explicit
`include_zero_activity=True` retains inactive entities in the denominator.
Null measures either fail or use explicitly requested zero substitution with
a warning. Maturity is explicitly `require_complete`, `include_partial`, or
`exclude_incomplete`; incomplete and excluded entities are counted and surfaced.

The operations `sum`, `mean`, `ratio_of_sums`, and `mean_of_ratios` are distinct.
For entity numerator/denominator components `(2, 1)` and `(8, 8)`, the ratio
of sums is `10/9` and the mean of ratios is `3/2`.
Zero denominators produce an undefined value or an error;
only a mean of ratios permits explicit exclusion, with diagnostics. Retained
component sums and entity/event counts make arithmetic inspectable.

`RatioRequest` divides independently computed compatible aggregate totals. It
does not join independent facts. It preserves selected numerator/denominator
quantity identities, their units, and the zero-denominator policy. Population,
grain, dimensions, period, inclusion policy, and observation window must match.

### Contrasts

`ContrastRequest` supports a level, comparison minus baseline, or relative
change. Units and selected quantity identities must match, as must population,
grain, dimensions, aggregation/measure semantics, observation-window policies,
and period duration. Relative change is undefined at a zero baseline; negative
baselines use signed division with an explicit warning. Different same-duration
periods
are allowed for period comparisons; partial observation availability must have
the same offset from the period end.

Derived contrasts retain both operand scopes, including their baseline periods.
They do not masquerade as aggregate totals. Expected date offsets, reconciliation
equations, experiment confidence levels, practical thresholds, and component
roles are part of compatibility, not just labels. Checks are deliberately
conservative and can reject equivalent but differently expressed semantics.

### Reconciliation

`ReconciliationRequest` checks each unique row's result against
`intercept + sum(coefficient[column] * value[column])`. Coefficients, unit,
absolute tolerance, and relative tolerance are explicit. A row fails only when
the absolute residual exceeds
`max(absolute_tolerance, relative_tolerance * max(abs(actual), abs(expected)))`.
There is no hidden currency tolerance. For a currency unit, a caller-supplied
absolute tolerance of `0.005` with zero relative tolerance ignores a residual
near `1e-13` while still detecting a discrepancy of `0.02`. Counts, maximum
residual, violating row IDs, the equation, and precision policy are retained.

### Binary experiments

`BinaryExperimentRequest` requires unique subject IDs, explicit control and
treatment labels, a binary outcome, confidence level, practical threshold in
rate units, period, and design assumptions. Duplicate subjects, missing/invalid
outcomes, invalid assignments, and empty arms fail. Direction is always
treatment minus control. The retained output includes both arm denominators,
successes, rates, effect, confidence interval, p-value, signed Cohen's h,
confidence level, threshold, and practical-significance boolean.

With rates `p_t`, `p_c` and arm sizes `n_t`, `n_c`, the interval is the unpooled
Wald interval for `p_t - p_c`, with standard error
`sqrt(p_t*(1-p_t)/n_t + p_c*(1-p_c)/n_c)` and the requested normal quantile.
The two-sided z test uses the pooled success rate under the null. Cohen's h is
`2*asin(sqrt(p_t)) - 2*asin(sqrt(p_c))`. Practical significance means
`abs(p_t - p_c) >= threshold`; it is separate from statistical significance.
Sparse/boundary arms warn about the normal approximation. Intervals are not
clipped. Executable arithmetic establishes no causal validity; design and
randomization assumptions remain explicit caveats. This is one documented
procedure, not an automatic statistical-test selector.

## Exact fixed-point values and P0.2 binding

P0.1's retained DECIMAL strings stay unchanged. The analytical layer interprets
a string as a fixed-point number only when its retained SQL column type is
`DECIMAL(precision, scale)` and the value fits that declaration. Arbitrary
numeric strings remain invalid. Decimal values become exact rational numbers;
aggregation, ratios, contrasts, and reconciliation use rational arithmetic,
without an intermediate binary float or context-dependent Decimal rounding.
Floating inputs retain their actual binary value for arithmetic.

Each quantity can retain an `ExactNumber` with integer numerator/denominator
strings. Every request explicitly chooses a `NumericPolicy`:

- `exact_only`: publish a JSON integer or exactly representable binary64 value.
  Other rationals retain their exact value but have `value=null` and a reason;
  they remain usable in subsequent arithmetic, but cannot bind a float claim.
- `nearest_binary64`: publish the nearest binary64 value for fractional outputs,
  retaining both the exact rational and the exact projection error. Integers
  remain integers. Overflow cannot silently become infinity.

Statistical transcendental outputs (intervals, p-values, Cohen's h) are explicitly
tagged `statistical_binary64`; this is separate from exact component arithmetic.

`analytical_binding(record, {claim_field: quantity_name}, result_index=0)` builds
the existing P0.2 `ComputationBinding`, with additive source `analytical_record`.
For example, `analytical_binding(record, {"value": "value"})` supplies a metric
without retyping its number. Only published quantity `/value` pointers are
accepted. Request literals, component metadata, and arbitrary exact-number
strings cannot be bound as computed floats. P0.2 copies the published number and
requires exact equality for any supplied number; it adds no comparison tolerance.
An integer such as `9007199254740993` remains exact in the record but is rejected
when conversion to the existing metric float field would lose precision.

This is an explicit projection into the existing metric schema, not a silent
Decimal cast. The analytical record is the lossless representation; legacy
float-valued metrics cannot represent every exact fixed-point result.

## Execution, provenance, and inspection

Each execution reserves one existing Python computation budget slot. SQL source
acquisition is charged separately by the unchanged SQL service. Success retains
one `run_analytical` tool event using execution-result contract `1.0`, with the
analytical record inside its output. Computation failures retain one failed event
with no result. Request schema rejection occurs before execution. Result output
uses the existing retained byte cap; oversized outputs fail rather than truncate.

Bindings revalidate the result digest and every input output digest and successful
status, recursively for derived computations. All source inputs must pass the
existing provenance checks. Failed/timed-out parents, altered values, and changed
inputs cannot establish successful analytical evidence. Existing evidence
inspection reads the retained record after reload without recomputation or
another computation-budget charge. Workspace schema `1.2` and execution contract
`1.0` remain unchanged; the analytical record has its own version `1.0`.

This bounded-input library adds no threads, subprocesses, retries, SQL rewriting,
agent tool registration, or new isolation infrastructure. It is not a replacement
for the existing SQL cancellation boundary or general Python sandbox.

## Verification and phase boundary

Hand-checkable fixtures cover repeated events, inactive entities, boundary dates,
maturity, duplicate joins, unequal group sizes, distinct ratio semantics, scope
rejection, missing grid cells, sparse dates, noise/material reconciliation,
meaningful/null/immaterial experiments, arm swaps, invalid data, exact Decimal
handling, P0.2 binding/tampering, retention, and failed execution. Metamorphic
checks rename relations/columns/dimensions, translate dates, and permute rows.
Import checks prohibit production scenario/evaluator/benchmark dependencies.

Checkpoint completion exposed scope omissions around inclusion policies, derived
baseline periods, grid expectations, reconciliation identities, and confidence
levels. Focused failing regressions preceded those fixes. These findings reinforce
the audit's need for explicit analytical scope; they do not change frozen Phase 2
results or demonstrate agent adoption. No frozen evidence or evaluator rules
were changed. Future runs require a new code/schema identity.

P1.1b still owns agent tool exposure, source acquisition/adoption workflows,
guidance, and proof that agents choose/use these primitives without benchmark
hints. P1.2 Critic changes and P1.3/E1 task/evaluator/blinding work remain deferred.
Large-result streaming, additional cadences, arbitrary arithmetic expressions,
statistical-test selection, and predictive/causal models are outside this layer.

### Verification results (2026-09-22)

- Focused `test_analytical_primitives.py` and `test_analytical_scope.py`:
  **41 passed**.
- P0.1/P0.2/P0.3 plus relevant schema, provenance, citation, SQL, runtime, and
  ledger tests: **197 passed**.
- Full `uv run pytest -m 'not live' -q`: **806 passed, 3 skipped, 17 deselected**.
  Docker integrations skipped because the local Docker socket is unavailable.
  One existing negative-test Pydantic serialization warning remains.
- Ruff lint, Ruff format check (191 files), and `git diff --check`: passed.
- AST dependency restrictions passed; importing the analytical execution service
  loaded no `scenarios`, `evaluation`, or `benchmark` modules.

Commands used `UV_CACHE_DIR=/tmp/data-science-agent-uv`. No paid/live/model calls
ran. The full test process exited normally. No agent registration, prompt,
evaluator, scenario, benchmark, or frozen-run file was changed.
