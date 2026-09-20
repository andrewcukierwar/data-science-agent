# P0.2: numerical result binding and selection (contract 1.0)

P0.2 uses the unchanged P0.1 execution payloads. A `ComputationBinding` names
one canonical `tool_event_id`, an output source, and an explicit mapping from
**each numerical claim field** to its supplying JSON pointer. Merely citing an
execution is insufficient. No metric registry, analytical operator, numerical
method, or new model tool is introduced.

## Authoring and resolution

`MetricComparison`, `StatisticalAssessment`, and the optional numerical
`Finding.value` carry `computation` and an application-assigned `result_id`.
Models supply the binding and qualitative/definition fields; numerical fields
can be null. Before persistence, shared application code reads the retained
execution output and fills every numerical field. Supplied non-null values
must agree exactly, without tolerances. Contradictions fail as
`ResultBindingError` (an evidence-provenance error), before final selection.

For example, a metric computed by SQL can use:

```json
{
  "tool_event_id": "<exact ID returned by run_sql>",
  "source": "sql_rows",
  "fields": [{"field": "value", "pointer": "/0/0"}]
}
```

`sql_rows` pointers address `ToolEvent.output.rows`, including a row index and
column index. They can descend into a retained structured cell. Only retained
cells are available; capture truncation cannot recover missing rows or establish
population completeness.

`python_stdout_json` pointers address a **single complete JSON document** printed
on retained stdout, e.g. `/estimate`, `/confidence_interval/lower`, `/p_value`.
JSON logs surrounding the document, duplicate object keys, and truncated stdout
are rejected. This uses P0.1 stdout as-is; no result-capture changes are needed.

Metric and finding bindings must supply `value`. Statistical bindings must supply
`estimate`, `confidence_interval.lower`, `confidence_interval.upper`, `p_value`,
`effect_size`, `confidence_level`, `practical_significance_threshold`, and
`practically_significant`. The latter is a JSON boolean; the others must be
finite JSON numbers. Missing, duplicate, or extra mappings fail. Strings,
booleans standing in for numbers, nonfinite values, failed events, aliases in
place of canonical event IDs, and mismatched source types fail. Conversion to
the existing float schema must preserve numeric equality (oversized integers
that would round are rejected). Decimal strings are not silently converted.

The supplying execution must retain P0.1 output contract `1.0` and pass the
existing source-lineage checks itself. All additional citations must also
resolve. Both Analyst and Statistician statistics receive these checks.
Lineage remains the existing conservative source inspection, not a proof that
the model chose the right population, statistical procedure, or output field.

The result ID is a SHA-256 identity of the resolved typed record (including its
binding, definition, and qualitative annotations), excluding the ID itself and
citation aliases. Metric labels/context use the existing normalization rules.
An ID supplied with altered contents fails validation. Repeated selection of the
same persisted record preserves the ID; a newly qualified/redefined record can
get a new ID while retaining the same explicit computation/field references.

## Selection and propagation

`LeadResult.selected_result_ids` selects complete persisted specialist metrics
or statistical assessments. Application code copies the records and retains
specialist-level caveats. Lead can also supply a new field-bound claim. Lead
selection no longer infers new-contract metrics from a prose finding's metric
label, nor silently replaces a bound comparison via the old metric reuse path.
Legacy reuse remains available only for legacy workspaces.

The Lead and generalist share finalization validation. Bound metric compilation
preserves the selected record's fields/identity rather than merging definition
context into it. Conflicting selected bound metrics or statistical assessments
fail before replacement of the final set. Candidate construction copies selected
statistics and caveats into `CriticCandidate`, and includes their evidence refs.
Critic performs the same binding/provenance/conflict checks before model review.

New workspace schema **1.2** requires numerical bindings. Its top-level
`metric_comparisons` and `statistical_assessments` are the selected set after Lead
finalization. `specialist_results` remains historical evidence. The additive
`statistical_assessment_history` retains previous and current selected assessment
versions; replacement or explicit deselection never erases that history.
History is not automatically reselected. Explicitly selecting two conflicting
current assessments still fails; there is no latest-wins conflict suppression.

The report revalidates the candidate before rendering and prints the exact float
representations, selected statistics, confidence interval and confidence level,
p-value, effect size, practical threshold/decision, method, assumptions, causal
qualification, dimensions, periods, definition context, evidence, result IDs,
and assessment/Lead/finding/recommendation caveats. Audit observations, warnings,
issues, limitations, date coverage, missingness, and recommendations are rendered
as well. The report does not ask a model to recreate numerical tables.

## Offline evaluation and compatibility

For schema 1.2, offline provenance evaluation checks exact field bindings against
persisted events; statistics evaluation reads only the selected top-level set.
Changing a persisted p-value while retaining its binding fails. The evaluator
result adds `numerical_result_contract_version: "1.0"` to declare these
binding/selection semantics separately from unchanged scenario numerical rules.
No expected identity, numerical tolerance, or task requirement is changed.

Old schemas (`1.0`, `1.1`, or unversioned records) load without migration,
recomputation, fabricated bindings, or rewritten evidence. Unbound records have
`computation=null` and `result_id=null`; reports label them **legacy unbound**.
They are accepted only by the explicitly legacy workspace path, with no claim
of numerical binding. They cannot be selected by a fabricated bound result ID
or promoted unbound into a new 1.2 workspace. Legacy evaluation retains its old
statistics/history semantics and original check set/scoring; its numerical
contract marker is null. Bound records, if present in an older workspace, are
still validated. Old-workspace compatibility is not an upgrade of its guarantees.

Existing deterministic tests that intentionally construct pre-binding fake
execution records explicitly create legacy fixtures. New P0.2 tests create
normal 1.2 workspaces and cannot use that compatibility escape hatch.

## Scope and limits

The original typed `p_value=1.0` substitution for computed
`1.9504926019459696e-13` is impossible under this bound path: the value is copied
from its exact persisted output field, and every downstream supplied mismatch
is rejected. That does not prove that the original calculation, mapping,
interpretation, or free-text numerical assertion is analytically correct.
Qualitative prose remains model-authored; this contract protects typed numerical
fields and their deterministic rendering.

P0.1 code was not redesigned. The additional numerical finding check closes an
otherwise available typed-scalar bypass of the same invariant. No audit evidence
or Phase 2 conclusion is changed. Date profiling/cancellation (P0.3), the P1.1
business/statistical operators, Critic policy/repair (P1.2), and task/evaluator
boundary fixes and scenario-ID blinding (P1.3/E1) remain deferred. No frozen v8
workspace, manifest, or result is modified, and no paid calls are required.

## Implementation map

- Schemas: `src/schemas/computation.py`, `metrics.py`, `statistics.py`,
  `findings.py`, `lead.py`, `validation.py`, `run_state.py`.
- Shared binding and agent integration: `src/agents/result_binding.py`,
  `analyst.py`, `statistician.py`, `lead.py`, `critic.py`, `output_contract.py`.
  Generalist uses the existing shared Lead finalizer without a second policy.
- Persistence/candidate/report: `src/orchestration/ledger.py`, `runner.py`.
- Offline contract/selection checks: `src/evaluation/contracts.py`, `engine.py`,
  `primitives.py`.
- Regression coverage: `tests/test_result_binding.py`. Compatibility fixtures:
  `tests/legacy_numerical_fixture.py` and explicit imports in earlier tests.

## Verification (2026-09-19)

- Focused P0.2 regression file: 25 tests. Covers p-value corruption; exact
  specialist/Lead/Critic/state/report/evaluator propagation; both finalizers;
  metric and finding mismatches; Analyst/Statistician provenance parity;
  supersession/deselection with preserved history; selected conflicts;
  forged identities, missing/duplicate mappings, bad pointers, aliases, source
  mismatch, failed/truncated output, duplicate JSON keys, wrong types and lossy
  integer conversion; legacy readability and unchanged legacy scoring checks.
- Relevant metric/statistics/provenance/Lead/Critic/generalist/evaluator/ledger/
  runner selection: **241 passed**.
- Full `uv run pytest -m 'not live' -q`: **740 passed, 3 skipped, 17 deselected**.
  The skips are the existing Docker integrations (Docker socket unavailable).
  One existing negative-test Pydantic serialization warning remains.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: passed (180 files).
- `git diff --check`: passed.

Commands used `UV_CACHE_DIR=/tmp/data-science-agent-uv` because the default uv
cache is outside the writable sandbox. No paid/live tests or model calls ran.
The production output-schema fingerprint changes automatically with these typed
schemas; any future benchmark must declare its new code/configuration identity.
