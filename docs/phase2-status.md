# Phase 2 implementation status and benchmark handoff

**Status date:** 2026-08-19
**Implementation:** Tasks 1–9, R1–R12, and R13–R19 complete

**Remediation:** R13–R19 are all implemented, and the reopened R6 deterministic
preflight was rerun in full at this revision. Two items keep the gate open: the
two opt-in live architecture canaries, which are paid and last passed before
R14–R19 changed lifecycle accounting, and the benchmark-validity Sol High code
review, which is user-triggered. Neither can be run unattended.

**Experiment:** Task 10 attempted; blocked before the paid matrix; no
analytical results published

This document records the Phase 2 work, the required pre-benchmark remediation,
the attempted paid pilots, and the exact boundary between tested infrastructure
and empirical results. It is a benchmark-attempt and handoff record, not a
successful architecture-comparison report.

## Completed Phase 2 Pre-Benchmark Remediation: R1–R12

The following remediation tasks were completed and deterministically verified
before the first Task 10 attempt. The later R13–R19 findings do not erase that
historical verification, but they reopen the final R6 gate.

1. **R1 — Make the evaluator architecture-neutral [P0].** Remove
   role-presence requirements from scoring and replace them with
   capability/output requirements. Add regression tests proving semantically
   equivalent single-agent and multi-agent outputs receive the same result.
2. **R2 — Harden evidence provenance [P1].** Only successful executions and
   successfully materialized/verified artifacts establish evidence. Add
   adversarial failed-SQL, failed-Python, failed-artifact, and unrelated-success
   tests.
3. **R3 — Cryptographically/deterministically bind workspaces to scenarios
   [P1].** Persist scenario ID/version, seed, expected source paths and hashes,
   and preferably benchmark/code revision; reject mismatches during offline
   evaluation.
4. **R4 — Separate evaluator errors from analytical failures [P1].** Add an
   evaluator-error state, propagate it through contracts and aggregation, keep
   it in operational/reliability counts, and exclude it from analytical-quality
   denominators without silently discarding it.
5. **R5 — Harden benchmark-run execution semantics [P2/P2/P3].** Use clean
   state or explicit attempt IDs with cumulative accounting on resume; require
   known pricing or an explicit `unknown-cost` acknowledgement beyond the
   pilot; and remove the misleading `configured-model` CLI default.
6. **R6 — Fix scenario-document integrity + full preflight (gate reopened).** Remove the false
   model-visible clean/no-injection assertion, test that injected scenarios do
   not retain baseline-only assertions, and run the complete deterministic
   preflight: tests, Ruff, architecture-neutral fixtures, workspace mismatch,
   failed evidence, evaluator exceptions, interrupted resume, unknown pricing,
   all 10 × 2 × 3 dry-run cells, and a benchmark-validity-focused Sol High code
   review. This is the final gate and must be rerun after any later remediation,
   including R13–R19.
7. **R7 — Make tool use capability-driven [P0] (implemented).** Remove unconditional SQL and
   Python presence gates. Express requirements as scenario-specific typed
   capabilities and test equivalent outputs across different valid tool mixes.
8. **R8 — Enforce identity at every offline boundary [P1] (implemented).** Match persisted
   workspace identity to selected rules and complete manifest identity for
   standalone, completed, and non-completed rescore paths. Keep unbound legacy
   evaluation explicitly outside benchmark use.
9. **R9 — Consolidate aggregation-safe rescoring [P1] (implemented).** Route APIs and CLIs
   through one per-record error-isolated implementation, then recompute
   aggregates and paired comparisons from the rescored records.
10. **R10 — Bind the pilot to its run record [P2] (implemented).** Verify a
    canonical run-record digest and re-derive usage, latency, pricing
    availability, and cost before permitting the remaining matrix. Unknown-cost
    acknowledgements are bound to the exact manifest, pilot ID, and record
    digest; tampering, including `null` to `0.0` cost substitution, is rejected.
11. **R11 — Persist append-only attempt history [P2] (implemented).** Retain
    every attempt ID, timing, outcome, usage/cost delta, and event attribution;
    reconcile those records to cumulative benchmark totals and expose the full
    history on benchmark records.
12. **R12 — Make offline outputs non-destructive and atomic [P2] (implemented).**
    Refuse input paths and existing outputs, consolidate exclusive writes, and
    retire or delegate unsafe legacy CLI behavior.

### Follow-up review disposition

| Remediation | Current disposition | Required closure |
| --- | --- | --- |
| R1 | Verified | R7 capability/tool-mix and architecture-equivalence regressions |
| R2 | Verified | Retain all four failed-evidence adversarial fixtures |
| R3 | Verified | Identity mismatch and source-tamper regressions |
| R4 | Verified | Evaluator-error denominator and taxonomy regressions |
| R5 | Verified | Explicit model, pricing gate, cumulative resume, and pilot semantics |
| R6 | Deterministic preflight rerun; gate open | Document integrity is now a code invariant with catalog-driven regressions; 501 tests, Ruff, all adversarial suites, Docker integrations, and 60 dry-run cells passed at this revision. Outstanding: both paid live canaries and the Sol High benchmark-validity review |
| R7 | Verified | Capability/tool-mix and architecture-equivalence regressions |
| R8 | Verified | Identity mismatch, source-tamper, corrupt/missing, and non-completed rescore refusals |
| R9 | Verified | Multi-record evaluator-crash, lifecycle, denominator, aggregate, and paired-comparison regressions |
| R10 | Verified | Pilot/run-record digest, metadata, cost, latency, and unknown-cost acknowledgement regressions |
| R11 | Verified | Append-only attempt, event-attribution, reconciliation, and interrupted-resume regressions |
| R12 | Verified | Same-path/alias, existing-output, evaluator-failure, and exclusive-publication regressions |

## Phase 2 Post-Pilot Remediation: R13–R19 (all closed; R6 rerun outstanding)

The 2026-08-19 deep review traced the paid-pilot failures through the retained
workspaces and identified seven additional tasks. All seven are now closed.
The reopened R6 preflight must be rerun at this revision before a new Task 10
manifest is frozen. `PROJECT_PLAN.md` contains their complete acceptance
criteria.

| Remediation | Priority | Status | Required closure |
| --- | --- | --- | --- |
| R13 — Make every analytical agent output strictly structured | P0 | Complete | Typed `MetricDimension` list replaces every open-ended dimension map, all six production agents build strict output schemas with no opt-out, invalid final output raises `AgentOutputContractError`, and both live architecture canaries passed on 2026-08-19 |
| R14 — Persist usage and cost across failed model calls | P0 | Complete | Usage is recorded at the response boundary and reconciled once per run on both success and failure paths; parse, turn-limit, and lifecycle failures keep their tokens; unreconcilable usage is marked incomplete and its cost published as unavailable rather than `$0.00` |
| R15 — Give the single-agent runner a complete attempt lifecycle | P1 | Complete | `GeneralistRunner` opens one attempt before agent execution and finishes it as completed, blocked, failed, or interrupted with matching timing, usage, cost availability, and error; resume appends without recounting; benchmark records built from the real runner expose non-null attempt identity and full history |
| R16 — Retain interrupted benchmark cells and resume them safely | P1 | Complete | An interrupted cell is persisted as a cancelled/interrupted record before the manifest aborts, retaining workspace, attempt history, partial usage, cost availability, and latency; the workspace status is reconciled to `cancelled`; denominators count it as an observed operational failure; explicit resume retries only cancelled cells and appends a new attempt |
| R17 — Make the preflight sensitive to benchmark outcomes | P1 | Complete | One shared outcome-sensitive smoke gate asserts completion, readable report persistence, accounted or explicitly unavailable usage, explicit cost, and a reconciled attempt history; live tests, both canaries, and deterministic failure fixtures all use it, and it rejects all four retained pilot workspaces |
| R18 — Preserve explicit blocked reasons and accurate failure taxonomy | P1 | Complete | Orchestration persists a typed `RunBlockReason` plus a readable detail for every non-completion; the benchmark maps it to an explicit category instead of hard-coding budget; blocked and cancelled runs stay operational observations rather than evaluator failures; the aggregate taxonomy reproduces per-record categories exactly |
| R19 — Calibrate the paid pilot across architectures and workload classes | P2 | Complete | The manifest freezes a pilot set with at least one stratum per architecture; `run_pilot` measures every stratum and retains per-pilot observations; the estimate is a stratified sum with an explicit range and a named scaling method; the gate verifies every stratum and refuses missing, failed, mismatched, or unreconciled evidence; manifest-declaration and output-schema digests force a new manifest version on any model, budget, matrix, pilot-set, or schema change |

The deterministic half of that preflight—Ruff, the full suite, the adversarial
suites, Docker integrations, and the 60-cell dry-run—was rerun successfully at
this revision. Task 10 remains blocked on the two remaining R6 items: both
opt-in live architecture canaries and the benchmark-validity Sol High code
review.

## What is implemented

### 1. Versioned evaluation contracts and manifests

Typed, architecture-neutral Pydantic contracts cover model-visible scenario
metadata, evaluator-only rules, evaluator results, benchmark run records, run
configuration, budgets, usage, cost, latency, lifecycle outcomes, architecture,
provider/model identity, revision, seed, workspace, and aggregate manifests.
Validation rejects incomplete or ambiguous records. Evaluator-only truth is a
separate type and cannot enter generated prompts or model-visible documents.
Persisted workspace loading resolves explicit scenario/evaluator versions and
returns an explicit compatibility error when a legacy layout cannot be mapped.

### 2. Generic zero-API offline evaluation

Reusable deterministic primitives evaluate lifecycle, numerical values,
metric-definition identity, root cause and non-drivers, data quality,
statistics, provenance, unsupported claims, and task completeness. Scenario
definitions compose these rules instead of cloning a canonical evaluator.

R7 adds typed, evaluator-only capability policies. Catalog scenarios declare
required outputs such as completed audit, structured metrics, typed statistical
analysis, critique, and verified evidence. Generic provenance no longer requires
both SQL and Python; valid tool mixes are covered by calibration fixtures.
The catalog evaluator version is `1.1` for this scoring change.

R8 makes persisted workspace identity authoritative at offline boundaries:
selected rules must match scenario/version/evaluator identity, and manifest
rescoring verifies manifest, run, architecture, repetition, seed, source, and
code-revision fields before any result classification.

`scripts/evaluate_workspace.py` evaluates one persisted workspace and derives
its scenario/version from the persisted identity, or requires an explicit
`--legacy-diagnostic --scenario-id ... --scenario-version ...` selection for
unbound legacy workspaces. Bound workspaces refuse selected-rule mismatches.
The benchmark runner and manifest evaluator verify the complete identity for
every record before scoring or classifying evaluator errors. Both manifest
rescore entry points delegate to the same canonical per-record rescorer, which
rebuilds aggregates and paired comparisons from the rescored raw records.
Benchmark manifests should use `scripts/run_benchmark.py offline-rescore`; both
that command and the legacy manifest CLI publish only exclusive atomic outputs.
Stable serialization supports
byte-for-byte repeatability where timestamps or equivalent semantics do not
require normalization. The final report, Critic, and evaluator use the same
compiled final metric set.

R12 centralizes offline output publication behind a same-filesystem temporary
file and exclusive atomic link. Canonical path checks reject relative and
symlink aliases before evaluation; existing destinations are never replaced.
The benchmark report, manifest rescore, pilot/plan persistence, and legacy
manifest CLI all use this boundary.

### 3. Scenario framework and catalog

The catalog uniquely resolves scenario ID/version pairs to deterministic
generators and evaluator definitions. Shared invariants cover keys, dates,
metric identities, documented nulls, economic identities, and observability of
ground truth. Source writing is deterministic. The original clean ecommerce
generator remains independently testable, and the canonical scenario retains
its model-visible inputs and expected answer.

The version `1.0` catalog contains ten scenarios:

1. `canonical-q2-profitability`
2. `retention-q2-deterioration`
3. `cogs-q2-margin-deterioration`
4. `discount-refund-q2-deterioration`
5. `missing-reporting-day`
6. `partial-latest-reporting-day`
7. `meaningful-ab-treatment-effect`
8. `no-effect-ab-experiment`
9. `significant-but-immaterial-ab-effect`
10. `channel-mix-confounding`

The business scenarios include plausible non-drivers, coherent cross-table
economics, and deterministic tolerances. Their evaluators reject correct-looking
numbers calculated with the wrong population, date basis, denominator, or
window. Prompts and ordinary business documents do not reveal injected answers.

The quality traps evaluate both injected-defect recall and false positives on
clean data. The experiment scenarios remain within basic V1 statistics and
check conclusions, confidence intervals/effect size, practical significance,
assumptions, and causal restraint against seeded known sampling properties.

### 4. Evaluator calibration

Hand-authored correct and adversarial persisted fixtures cover pass, missing,
incorrect, conflicting, stale, unsupported-claim, wrong-denominator,
grain-multiplying-join, period-leakage, unsupported-causality,
evidence-free-number, and incomplete-but-keyword-rich cases. Correct fixtures
pass every evaluator; each targeted defect fails through its intended rule.
Catalog-wide regression tests protect evaluator changes.

### 5. Fair single-agent baseline

The generalist uses the existing Agents SDK, `AgentRunContext`, persisted
workspaces, DuckDB/Python/artifact services, bounded budgets, evidence schemas,
final metric compiler, and report contract. It owns audit, analysis,
statistics, self-critique, and synthesis and has no permission or construction
path for invoking the five specialists.

The comparison holds raw scenario inputs, documentation, question,
provider/model configuration, deterministic tools, sandbox boundaries,
structured findings, metrics, evidence, reports, and evaluator contracts
constant. Architecture-specific calls and tokens remain measured rather than
forcing identical internal workflows. Live smoke tests are opt-in and excluded
from normal CI.

### 6. Resumable runner and reporting

The benchmark matrix is scenario × architecture × repetition. Planning writes
an immutable manifest before execution and derives unique run IDs. Resume skips
completed cells, preserves failed cells, isolates operational/provider failures,
and refuses to overwrite an existing workspace. Offline rescoring creates a new
manifest without rerunning an agent.

Paid execution requires all of the following:

- explicit `--allow-paid`;
- `OPENAI_API_KEY` in the runner process environment;
- `OPENAI_DEFAULT_MODEL` exactly matching the frozen manifest model;
- a persisted cost-estimation pilot before the full matrix.

R10 makes the pilot a derived, versioned view of one completed manifest run
record. The report carries the manifest/model/configuration identity, complete
usage and latency observations, cost availability, and a canonical SHA-256
digest of that record. The full-run gate re-derives and compares those values,
and persists any unknown-cost acknowledgement against the exact pilot ID and
record digest. Known pricing must include the recorded pricing model and cost
breakdown; `null` cost cannot be replaced with `0.0` to bypass the gate.

R11 persists typed attempt records with terminal outcomes, usage and cost
deltas, elapsed time, and event attribution. Resume appends a new attempt while
preserving completed history; interrupted attempts are explicitly closed and
their persisted deltas are not counted again. Benchmark records expose the
complete attempt history, while unknown attempt cost remains unavailable rather
than being coerced to zero.

Plan, dry-run, offline evaluation, rescore, and report modes require no
credentials and intentionally do not load `.env`. The first declared benchmark
defaults to three repetitions per scenario and architecture; fewer repetitions
require a written cost-calibration justification in the manifest.

Aggregation retains every raw record and denominator, including failed or
missing cells. It reports completion and evaluation rates, score distributions,
small-sample Student-t intervals where possible, paired differences for matched
scenario/configuration runs, cost, latency, and failure taxonomy. Comparisons are
labeled as descriptive, supported, unsupported, or insufficient-sample rather
than compressed into one opaque score. Deterministic JSON output is suitable
for later README tables without manual transcription.

### 7. Strict structured agent output (R13)

Segment dimensions are a typed `MetricDimension` list of `name`/`value` pairs
instead of an open-ended JSON object, so `MetricObservation`,
`MetricComparison`, `MetricConflict`, `StatisticalAssessment`,
`StatisticalExpectation`, and evaluator ground truth share one
strict-compatible representation. Normalization sorts and aliases dimension
names deterministically and rejects repeated names, so an equivalent
measurement always round-trips to exactly one canonical form and the estimand
seen by the evaluator is unchanged.

Every production agent — Generalist, Lead, Analyst, Statistician, Data Auditor,
and Critic — builds its output through `agents.output_contract`. The strict
schema is compiled when the agent is constructed, so an incompatible output
type fails locally instead of during a paid request, and no agent passes
`strict_json_schema=False`. Final output is no longer re-parsed permissively:
anything that is not already the declared type raises
`AgentOutputContractError`, a `ModelBehaviorError` subclass, and the Lead's
specialist-output extractor raises rather than forwarding raw text.

Workspaces persisted before R13 stored the mapping form. They still load
through an explicit compatibility coercion at the schema boundary, so retained
pilot evidence under `.runs/` remains offline-evaluable without weakening the
strict wire contract.

### 8. Failure-safe usage and cost accounting (R14)

Model usage is recorded when each provider response arrives, through the shared
`ModelUsageHooks.on_llm_end` hook, and every agent run goes through
`run_agent_with_usage`. That wrapper reconciles the usage it recorded against
the run's authoritative cumulative total on both the success and the exception
path, recording only the remainder, so a response is never counted twice and
never dropped. The Agents SDK attaches that cumulative total to
`run_data.context_wrapper` for `ModelBehaviorError` and `MaxTurnsExceeded`, so
the exact pilot failure — an invalid-JSON final output — now keeps every token
the provider reported and billed.

When no authoritative total is available, the ledger marks the run and its
active attempt as having incomplete usage and refuses to publish a cost: both
become `unavailable` with an explicit note. Known pricing over incomplete usage
can therefore no longer be published as `$0.00`. Benchmark run records expose
the same facts through `UsageSummary.complete` and the ledger's cost note.

Incomplete usage reuses the existing `unavailable` cost representation rather
than introducing a third availability state, so the R10 unknown-cost
acknowledgement gate, the attempt-cost reconciliation, and aggregation keep
their verified semantics.

This applies to runs executed from now on. The retained Task 10 pilot records
keep their historical values, including the `$0.00372068` that the status table
below already describes as an incomplete lower bound.

### 9. Single-agent attempt lifecycle (R15)

`GeneralistRunner` runs under the same append-only attempt protocol as the
multi-agent runner. One attempt opens after the ledger is constructed and
before any agent context is built, so the run configuration carries the attempt
ID and every agent event, tool event, usage delta, and cost belongs to it.
Completed and blocked exits close it as `COMPLETED` or `BLOCKED`, the failure
handler closes it as `FAILED` with the same message persisted in the run state,
and a `BaseException` exit such as `KeyboardInterrupt` closes it as
`INTERRUPTED`. Runtime metadata is finalized before the attempt is closed, so
the terminal record carries usage, cost availability, and elapsed time.

Resume appends a new attempt through the ledger's existing append-only
semantics: a prior terminal record is never rewritten, an attempt left running
is explicitly closed as interrupted, and cumulative run totals remain the sum of
the per-attempt deltas. Single-agent benchmark records therefore expose the same
non-null attempt identity and full attempt history the multi-agent architecture
already published, which matters because the benchmark compares the two
architectures on identical output and evidence contracts.

### 10. Retained and resumable interrupted cells (R16)

The retained `v2` attempt is the concrete failure this closes: its workspace
still holds 34 requests and an interrupted attempt, but the manifest recorded
zero run records, so the aborted report observed nothing and counted 60 missing
cells.

Interrupting a declared cell now writes a cancelled record — persisted while the
manifest is still `running`, before it is marked `aborted` — carrying the
workspace path, attempt history, partial usage, cost availability, latency, and
an explicit interruption reason under the `interrupted` failure category.
Aggregation therefore counts the cell as an observed operational failure rather
than a missing repetition. Both runners reconcile the workspace's top-level
status to `cancelled` so an interrupted workspace no longer advertises
`running`.

An explicit resume retries only cancelled cells. Completed, failed, and blocked
records are genuine observations of the system under test and are never
re-executed, because replacing them would quietly discard the evidence the
benchmark exists to report. A retried cell keeps its immutable run ID and
workspace, and appends a new attempt while the interrupted attempt stays in the
history verbatim. A cost pilot cannot be published from an interrupted cell.

Retained pre-R16 evidence is not rewritten: the `v2` manifest still records zero
cells and its workspace still reads `running`, as the Task 10 attempt table
below describes.

### 11. Outcome-sensitive preflight gate (R17)

The previous preflight was green while every retained pilot failed, because its
live smoke assertions checked permissive configuration and artifact presence: a
ledger exists, some agent events were recorded. A run that produced invalid
JSON, lost its usage, or dropped an interruption satisfied them.

`benchmark/preflight.py` replaces that with one shared gate asserting the
outcomes a paid matrix actually requires:

- the run completed, with no error, and the persisted status matches the
  returned status;
- a report artifact was returned, persisted as the run's final report, and is
  readable and non-empty on disk;
- usage is nonzero, or explicitly published as incomplete with a reason — a
  completed run silently recording zero tokens fails;
- cost is either a known breakdown or an explained unavailability, and is never
  a known breakdown over incomplete usage;
- attempt history exists, its identity matches the run, no attempt is left
  running, and its usage and elapsed deltas reconcile to the run totals;
- the architecture's role boundary holds.

Both live lifecycle smoke tests, both live canaries, and the deterministic
failure fixtures call the same function, so the assertions that authorize a
paid pilot are exactly the ones proven to reject broken runs. The fixtures
drive the production runners to real outcomes and prove that invalid JSON, lost
usage, a dropped interruption, a missing attempt history, unreconciled attempt
usage, and a deleted report file all fail.

The gate is calibrated against real evidence, not invented thresholds: a
regression runs it against all four retained Task 10 pilot workspaces and
requires every one to fail. The failures reproduce the documented modes exactly
— all four fail completion and report persistence, and the two single-agent
pilots additionally fail `usage:accounted` and `attempts:recorded`, the losses
R14 and R15 fixed.

R17's remaining acceptance item is the full R6 rerun. At this revision Ruff,
the Docker-backed integration tests, the adversarial fixtures, and the 60-cell
dry-run all pass; the two opt-in live canaries have not been rerun since R14–R19
changed lifecycle accounting.

### 12. Explicit block reasons and accurate failure taxonomy (R18)

Every blocked analysis used to be recorded as a budget failure, and every other
non-completion had its category guessed by matching substrings in an error
message. A self-critique that still required revision, an unresolved follow-up,
a schema violation, and an interruption were all published as `budget`, which
would have made the Task 10 failure taxonomy actively misleading.

Orchestration now persists a typed `RunBlockReason` and a human-readable
`block_detail` for every non-completion. Classification happens where the
condition is known:

| Condition | Reason | Benchmark category |
| --- | --- | --- |
| Run resource budget exhausted | `budget_exhausted` | `budget` |
| Critic/self-critique still REVISE | `validation_revision` | `validation` |
| Objective-critical follow-up unresolved | `unresolved_follow_up` | `unresolved_follow_up` |
| Structured-output violation | `schema_failure` | `schema` |
| Agent turn limit reached | `agent_failure` | `agent` |
| Mandatory audit blocked | `data_quality` | `data_quality` |
| User or provider interruption | `interrupted` | `interrupted` |

Only `BudgetExhaustedError` is budget exhaustion. A turn limit is an agent
bound, not the configured resource budget, and a structured-output violation is
a schema failure — the distinction the retained pilots needed and did not have.

`BenchmarkRunner` reads the persisted reason instead of hard-coding
`FailureCategory.BUDGET` for blocked runs; prose inference survives only for
pre-R18 workspaces that carry no reason. Blocked and cancelled records keep
`NOT_EVALUATED` rather than `FAIL`, so they stay operational observations inside
the denominators and are never silently converted into analytical evaluator
failures. The aggregate taxonomy is asserted to reproduce the per-record
categories exactly.

Retained evidence is not rewritten: the two retained invalid-JSON pilot records
still read `failed / other`, because their workspaces predate the persisted
reason. An equivalent run today records `schema_failure` / `schema`.

### 13. Stratified pilot-set calibration (R19)

The retained attempts extrapolated all 60 cells from one first cell, so no
architecture or workload difference could surface and one measurement stood in
for the whole matrix.

Planning now freezes a pilot set into the manifest — one stratum per declared
architecture by default, with explicit workload strata available by naming
scenario IDs — and the manifest validator requires every architecture to be
represented and the strata to partition the matrix. `run_pilot` measures one
cell per stratum and writes a version-2.0 report that retains every per-pilot
observation bound to its immutable run record.

The estimate is a stratified sum: each stratum contributes mean-per-cell times
its planned cells, with a low/high range from the observed per-stratum minimum
and maximum, and the scaling method named in the report. One unknown stratum
makes the whole matrix cost unavailable rather than silently understated.

The full-run gate verifies every declared stratum and refuses a pilot whose
record is missing, did not complete, belongs to another stratum, or whose usage,
cost, or latency no longer reconciles with the immutable record; it also
recomputes the matrix estimate from the retained observations. Two fingerprints
force re-planning: the manifest-declaration digest covers model identity, turn
budgets, matrix size, and the declared pilot set, and the output-schema
fingerprint covers the production agent output contracts. Mutable execution
state — records, aggregates, status, and the per-scenario source identities R8
verifies separately — is excluded, so a matrix run cannot invalidate its own
pilot. Unknown-cost acknowledgement binds every affected pilot record digest.

## Verification completed

### Reopened R6 preflight, deterministic half, rerun at this revision

```text
501 passed, 16 deselected
```

The deselected tests are opt-in live tests. Ruff lint reported `All checks
passed!` and format reported 148 files already formatted. Each declared
preflight category was also run as an explicit selection rather than being
inferred from the whole-suite result: architecture-neutral evaluator fixtures,
corrupted/mismatched-workspace and source-tamper refusals, failed-evidence
adversarial fixtures, evaluator-exception isolation, interrupted-resume across
the benchmark runner and both orchestration runners, unknown-pricing and
unknown-cost acknowledgement, and the scenario-document integrity regressions.
The R17 outcome gate passed, including its calibration against all four
retained Task 10 pilot workspaces. Docker-backed integration tests executed
real containers. The complete 10 × 2 × 3 declaration produced 60 cells with 60
unique run IDs and 60 unique workspace paths, and the R19 pilot set partitioned
them 30 per architecture. Every retained `.runs/` artifact still loads at this
revision — 10 benchmark manifests and 18 ledger states — and offline rescore
and report still run against retained benchmark evidence.

Two R6 items remain open because neither can be run unattended: the two paid
opt-in live architecture canaries, and the benchmark-validity Sol High code
review, which is user-triggered.

### Scenario-document integrity is now a code invariant (R6)

One generated document is shared by the clean baseline and by every scenario
injected on top of it, so a sentence asserting injection status is true for at
most one of them and is a false premise for every other. That is exactly how
the original model-visible clean/no-injection claim survived: it was written
once for the baseline and inherited unchanged by seven injected scenarios.

`scenarios/invariants.py` now declares `BASELINE_ONLY_DOCUMENT_CLAIMS` and
raises `document:injection-status-claim` from `check_dataset_invariants`, so
every scenario suite enforces it during `generate_validated` rather than only
in a test. The regression in `tests/test_scenario_catalog.py` is parametrized
from `discover_scenarios()` instead of a hard-coded list — it previously
covered seven of ten registered scenarios — and also checks the model-visible
context contract. Reintroducing the exact historical sentence into the
generator was verified to fail seven ecommerce-family scenarios plus the
baseline check; the three experiment scenarios use a separate document and are
correctly unaffected.

### Earlier preflight, superseded

An earlier preflight was green before live pilot evidence existed. The
subsequent post-pilot deep review found the R13–R19 release blockers in
structured output, failure-path accounting, Generalist attempts, interruption
persistence, outcome assertions, failure taxonomy, and pilot
representativeness. That run is historical evidence only.

### Live strict-output canaries (R13)

Both opt-in canaries were run on 2026-08-19 against the configured live model
and passed:

```text
uv run pytest -m live tests/test_strict_output_canary_live.py
3 passed in 74.01s (0:01:14)
```

The multi-agent and single-agent canaries each completed their top-level strict
output contract — typed `AuditResult`, `LeadResult`, and `ValidationResult`, with
recorded requests and elapsed time — with no strict-schema or final-output
parsing failure. This is the first live evidence that the contract holds against
a real provider; the four retained Task 10 pilots all failed here. It is a
point-in-time observation for the current output types, so the canaries must be
rerun as part of the reopened R6 preflight after R14–R19.

## Task 10 attempt — blocked before the paid matrix

Task 10 was attempted on 2026-08-19 after the final R6 preflight. Four immutable
manifest versions and their raw workspaces were retained under
`.runs/phase2-task10-20260819/`:
The runner's `multi-agent` label denotes the planned five-agent architecture.

| Manifest | Model | Persisted run records | Pilot/operational result |
| --- | --- | ---: | --- |
| `phase2-task10-20260819-luna-v1` | `gpt-5.6-luna` | 1/60, multi-agent | Invalid JSON; persisted 28,825 tokens and `$0.00372068` omit the failed Lead response |
| `phase2-task10-20260819-luna-v2` | `gpt-5.6-luna` | 0/60 | Interrupted after partial progress; workspace retains 34 requests and an interrupted attempt, but the manifest dropped the cell |
| `phase2-task10-20260819-luna-v3` | `gpt-5.6-luna` | 1/60, single-agent | Invalid JSON after 71.98 s; failed-call usage was lost and known pricing became `$0.00` |
| `phase2-task10-20260819-gpt55-v1` | `gpt-5.5` | 1/60, single-agent | Invalid JSON after 61.79 s; failed-call usage was lost and pricing was unavailable |

Every plan declared the same 60 cells (ten scenarios × two architectures ×
three repetitions). Since no pilot completed, the full-run gate correctly
refused to execute the remaining cells. The retained artifacts include the
original manifests, pilot reports where finalized, failed workspaces, and
failure-only offline-rescored manifests and reports:

- `benchmark.json`, `pilot.json`, and
  `benchmark-v1-rescored-after-r9-fix.json` /
  `report-v1-rescored-after-r9-fix.json`;
- `benchmark-v2.json` and `report-v2.json` (aborted before a pilot report);
- `benchmark-v3.json`, `pilot-v3.json`, and
  `benchmark-v3-rescored-after-r9-fix.json` /
  `report-v3-rescored-after-r9-fix.json`;
- `benchmark-gpt55.json`, `pilot-gpt55.json`, and
  `benchmark-gpt55-rescored-after-r9-fix.json` /
  `report-gpt55-rescored-after-r9-fix.json`.

The three persisted failed records are operational failures and are explicitly
`not_evaluated` after the R9 rescore correction; they have no analytical score.
Each corresponding report observes one failed record and 59 missing cells,
while the aborted v2 report observes zero records and 60 missing cells. There
are therefore no honest task-success, numerical-accuracy, unsupported-claim,
latency-distribution, cost-distribution, or architecture-comparison values to
publish. The persisted `$0.00372068` is incomplete because usage from the
failed Lead response was not recorded, so it is a lower bound rather than a
valid pilot cost. The
observed one-failure-per-attempt outcome is a pilot calibration observation,
not a full-matrix reliability estimate. Existing canonical MVP workspaces are
legacy acceptance artifacts and were not substituted for missing cells.

The publishable observations are limited to the following:

| Dimension | Observed evidence | Benchmark interpretation |
| --- | --- | --- |
| Task success | 0 completed/evaluable pilot records | Not estimable; no success rate claimed |
| Numerical accuracy | 0 analytical scores | Not estimable |
| Unsupported claims | 0 evaluated reports | Not estimable |
| Operational reliability | 3 persisted pilot records failed; v2 workspace retained an interrupted attempt that its manifest omitted | Calibration evidence only; R16 must restore the missing operational record |
| Cost | v1 persisted `$0.00372068` but omitted the failed Lead response; v2 workspace retained `$0.0360329`; single-agent failed-call usage was lost | Accounting is incomplete; no valid pilot or full-run cost estimate |
| Latency | 120.38 s (v1), 71.98 s (v3), 61.79 s (gpt55) | Failed-pilot elapsed times only |
| Architecture comparison | No matched completed pair | Not estimable |

The repeated invalid-JSON outcome across models and architectures points to the
permissive output-schema integration, not simple API availability or one model
identity. No evaluator rule or score was changed to make an attempt pass, and
no result was selected because it favored either architecture. R13–R19 must be
implemented and the reopened R6 gate must pass before a new manifest and pilot
set are frozen under the same offline-evaluation rules.

## Diagnosed local execution issues

### VS Code environment inheritance

The active VS Code agent process reported both variables absent, including when
checked through the project Python interpreter:

```text
OPENAI_API_KEY=absent
OPENAI_DEFAULT_MODEL=absent
```

Exporting variables in VS Code's integrated terminal affects only that shell and
processes launched by it. It does not update an extension host that is already
running. The safe setup is:

1. Fully quit VS Code.
2. Open macOS Terminal and export the values there.
3. Launch the repository with `code /path/to/data-science-agent` from that same
   shell.
4. Start a new agent conversation in the reopened window.

```bash
export OPENAI_API_KEY="<your key>"
export OPENAI_DEFAULT_MODEL="<model name>"
code /path/to/data-science-agent
```

Use the VS Code Command Palette action **Shell Command: Install 'code' command
in PATH** if the launcher is unavailable. Do not paste the key into chat or
commit it. Check presence without revealing it:

```bash
test -n "$OPENAI_API_KEY" && echo "API key is set"
printf '%s\n' "$OPENAI_DEFAULT_MODEL"
```

### Docker and sandbox access

The initial in-sandbox Docker check failed with permission denied against the
Docker Desktop Unix socket. The socket and parent directories were owned by the
current user with usable Unix permissions, the active context was
`desktop-linux`, and `DOCKER_HOST` was unset. A read-only `docker info` outside
the restricted sandbox succeeded and reported a healthy Docker Desktop 29.7.2
server on ARM64.

The Docker error is therefore a Codex sandbox restriction, not a stopped daemon,
wrong context, or repository defect. Live Docker commands from the agent require
explicit elevated approval. The analysis containers themselves must retain
their no-network security boundary.

The restricted sandbox also prevented `uv` from opening its default cache under
the user cache directory. Direct use of `.venv/bin/python` succeeded. This cache
error did not cause the missing environment variables and should not be confused
with credential detection.

## Remaining benchmark sequence

Before another paid attempt:

1. R13–R19 are implemented, with their focused deterministic regressions in
   `tests/test_strict_agent_outputs.py`,
   `tests/test_model_usage_accounting.py`,
   `tests/test_generalist_attempt_lifecycle.py`,
   `tests/test_benchmark_interruption.py`,
   `tests/test_preflight_smoke_gate.py`,
   `tests/test_failure_taxonomy.py`, and
   `tests/test_pilot_set_calibration.py`. No evaluator rule was altered to
   mask the retained failures.
2. The deterministic half of the reopened R6 preflight has been rerun at this
   revision — strict-schema checks, failure-path accounting, lifecycle and
   taxonomy fixtures, scenario-document integrity, Docker integrations, Ruff,
   and all 60 dry-run cells.
3. Rerun the two bounded live strict-output canaries in
   `tests/test_strict_output_canary_live.py` (one per architecture), which
   require completion, report persistence, usage accounting, and attempt
   history through the R17 gate. They passed for R13 on 2026-08-19, before
   R14–R19 changed usage accounting, the Generalist attempt lifecycle, and
   interruption persistence, so that result no longer covers this revision.
   These are paid runs.
4. Run the benchmark-validity-focused Sol High code review. It is
   user-triggered and cannot be launched from a session. Steps 3 and 4 are the
   only remaining R6 items.
5. Recheck variable presence without printing the key and confirm Docker access.
6. Select the compatible model and freeze a new manifest before any paid execution.
7. Review the declared ten-scenario × two-architecture × three-repetition matrix,
   budgets, evaluator versions, code revision, and output paths.
8. Run and retain the R19 pilot set, including at least one cell per architecture.
9. Decide whether the declared repetition count remains affordable. If it must
   change, create a new manifest/version and record the justification before the
   full run.
10. Resume the immutable matrix with `--allow-paid`; do not use `--force` or
    overwrite workspaces.
11. Evaluate every persisted workspace offline using the frozen evaluator rules.
12. Inspect failures without changing rules mid-experiment. If code or evaluator
    changes are required, version the benchmark and rerun the affected declared
    matrix rather than silently patching scores.
13. Generate and retain raw manifests, run records, evaluator results, pilot
    report, and aggregate report.
14. Publish real results and limitations, including denominators, failed runs,
    sample size, model specificity, evaluator limitations, uncertainty, cost,
    and latency—regardless of which architecture performs better.
