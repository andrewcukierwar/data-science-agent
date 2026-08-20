# Phase 2 implementation status and benchmark handoff

**Status date:** 2026-08-20
**Implementation:** Tasks 1–9 and R1–R25 complete; renewed final R6 gate closed

**Remediation:** R13–R19 are all implemented. The reopened R6 deterministic
preflight and benchmark-validity review were rerun at this revision. Fresh paid
canaries ran on 2026-08-20: single-agent passed; multi-agent failed its
executed-evidence gate. R20–R25 track the resulting audit/Lead provenance
defects. R20 and R21 are now implemented: audit contract 2.0 carries typed
evidence-bearing claims, one persistence boundary refuses unsupported completed
audits, the Lead receives a bounded typed audit evidence catalog, and offline
scoring enforces the same provenance boundary at catalog evaluator version 1.2.
R22 adds one shared hypothesis-evidence rule, enforced when the transition is
requested rather than after the final model response, and R23 adds one bounded,
tool-less correction attempt for a strict-schema-valid response whose citations
do not resolve, and R24 replaces four drifted provenance implementations with
one lossless citation-resolution contract, and R25 names semantic citation
failures and retains the canary as a deterministic regression. The complete R6
preflight is now closed: 681 deterministic tests including three Docker
integrations, Ruff, retained-artifact validation, all 60 dry-run cells, and both
provider-backed architecture canaries passed at the final revision. The live
gate also exposed and closed valid-JSON datetime parsing, an unconditional
multi-agent visualization requirement, and provider-visible empty audit
provenance lists.

The first post-R6 R19 pilot retained a blocked multi-agent cell after the Critic
found stale same-scope CAC evidence in a remediated candidate. The cell remains
operational calibration evidence and no later stratum or matrix cell ran.
Decision 0015 fixes the three application boundaries it exposed, and the full
R6 deterministic, Docker, Ruff, and provider-backed gates were rerun before a
replacement manifest could be frozen.

**Experiment:** Historical Task 10 attempts retained; new manifest/pilot ready;
paid matrix not executed; no analytical results published

This document records the Phase 2 work, the required pre-benchmark remediation,
the attempted paid pilots, and the exact boundary between tested infrastructure
and empirical results. It is a benchmark-attempt and handoff record, not a
successful architecture-comparison report.

## Completed Phase 2 Pre-Benchmark Remediation: R1–R12

The following remediation tasks were completed and deterministically verified
before the first Task 10 attempt. The later R13–R19 and R20–R25 findings do not
erase that historical verification, but they reopen the final R6 gate.

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
6. **R6 — Fix scenario-document integrity + full preflight (complete).** Remove the false
   model-visible clean/no-injection assertion, test that injected scenarios do
   not retain baseline-only assertions, and run the complete deterministic
   preflight: tests, Ruff, architecture-neutral fixtures, workspace mismatch,
   failed evidence, evaluator exceptions, interrupted resume, unknown pricing,
   all 10 × 2 × 3 dry-run cells, and a benchmark-validity-focused Sol High code
   review. This is the final gate and must be rerun after any later remediation,
   including R13–R19 and R20–R25.
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
| R6 | Verified / closed | After R20–R25 and decision 0015, 681 deterministic tests including Docker, Ruff, retained artifacts, the 60-cell dry run, and both provider-backed architecture canaries passed; every live integration defect was dispositioned before the final green run |
| R7 | Verified | Capability/tool-mix and architecture-equivalence regressions |
| R8 | Verified | Identity mismatch, source-tamper, corrupt/missing, and non-completed rescore refusals |
| R9 | Verified | Multi-record evaluator-crash, lifecycle, denominator, aggregate, and paired-comparison regressions |
| R10 | Verified | Pilot/run-record digest, metadata, cost, latency, and unknown-cost acknowledgement regressions |
| R11 | Verified | Append-only attempt, event-attribution, reconciliation, and interrupted-resume regressions |
| R12 | Verified | Same-path/alias, existing-output, evaluator-failure, and exclusive-publication regressions |

## Phase 2 Post-Pilot Remediation: R13–R19 (all closed)

The 2026-08-19 deep review traced the paid-pilot failures through the retained
workspaces and identified seven additional tasks. All seven are now closed.
The reopened R6 deterministic preflight and validity review have been rerun at
this revision. `PROJECT_PLAN.md` contains the complete acceptance criteria.

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
suites, Docker integrations, retained-artifact rescoring, and the 60-cell
dry-run—was rerun successfully at this revision. The benchmark-validity review
is also complete. The failed multi-agent canary subsequently opened R20–R25, so
Task 10 was blocked until those tasks and a fresh complete R6 preflight closed;
both conditions are now satisfied.

## Phase 2 Live-Canary Provenance Remediation: R20–R25 closed

The 2026-08-20 provider-backed R6 run passed the single-agent canary but failed
the multi-agent canary after valid strict output. The Data Auditor's successful
tool provenance was not represented in its typed limitations, the Lead was told
to treat the provenance-free audit JSON as evidence, and resolved hypothesis
`H2` cited the fabricated reference `completed_data_audit`. A focused code
review found six remediation areas. `PROJECT_PLAN.md` contains their full
acceptance criteria.

| Remediation | Priority | Status | Required closure |
| --- | --- | --- | --- |
| R20 — Preserve typed audit provenance across architecture boundaries | P0 | Complete | Audit contract 2.0 gives table profiles, warnings, issues, and limitations typed `evidence_refs`; `persist_audit_result` canonicalizes them and refuses a non-blocked audit with missing, failed, ambiguous, or fabricated provenance; the Lead receives a bounded typed `AuditEvidenceCatalog` instead of raw audit JSON; persisted state is versioned `1.1` and the output-schema fingerprint forces a new manifest |
| R21 — Enforce audit provenance in capability and offline scoring | P0 | Complete | `resolve_audit_claims` applies the executed-evidence boundary offline; the data-audit capability needs supported material claims, not a completed status; every required issue ID needs its own `required_provenance` check; a clean audit must show an executed check; catalog evaluator version advanced to `1.2` |
| R22 — Align hypothesis evidence contracts and validate state transitions | P1 | Complete | `hypothesis_requires_evidence` is the one shared predicate for the contract, state tool, final Lead validation, and offline evaluation; `record_hypothesis` refuses an unsupported resolution before touching the ledger and returns an actionable typed error; open hypotheses stay usable; the append-only history is checked offline |
| R23 — Add bounded correction for semantic provenance failures | P1 | Complete | `evidence_correction_attempts` is validated `ge=0, le=1`; the correction agent has no tools and one turn; the request names the invalid field IDs and a bounded citable-evidence catalog; the corrected response passes the identical persistence boundary; both calls, their usage, and their outcomes bind to the active attempt; a second invalid response terminates. Both architectures get the same allowance |
| R24 — Make citation resolution lossless and consistent | P1 | Complete | `resolve_citations` returns resolved and unresolved explicitly and `canonical_references` drops nothing; a claim is supported only when every citation resolves; `material_claims` is one shared definition; the Lead's private resolver is deleted and the Critic now checks resolution; qualitative-finding and source-lineage rules apply offline too |
| R25 — Classify provenance failures and close the live regression gap | P2 | Complete | `EvidenceProvenanceError` is the shared base; `RunBlockReason.EVIDENCE_PROVENANCE` and `FailureCategory.EVIDENCE_PROVENANCE` propagate through attempt history, benchmark records, aggregation, failure reports, and canonical offline rescore; the 2026-08-20 handoff is reproduced deterministically; lifecycle fixtures use evidence-bearing audits; both paid live canaries pass at the final R6 revision |

R20–R25 must preserve R2 and R7: the solution may carry and validate provenance
across agent boundaries, but it may not accept an audit merely because a role
ran, prescribe an unnecessary tool, silently replace model citations, or retry
until a favorable output appears.

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

R17's deterministic acceptance evidence is complete. At this revision Ruff,
the Docker-backed integration tests, the adversarial fixtures, and the 60-cell
dry-run all pass. The 2026-08-20 live rerun passed single-agent and failed
multi-agent at the executed-evidence check, proving the shared gate rejects a
semantically unsupported Lead output.

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

### 14. Typed audit provenance across architecture boundaries (R20)

The Data Audit was the one material output that could influence a candidate
answer without carrying provenance. `TableAudit` asserted row counts, date
coverage, duplicate rates, and missingness with no references; table warnings
and run limitations were bare strings. That is invisible in the single-agent
architecture, where the generalist still holds its own tool results, and fatal
in the multi-agent one, where the Lead has no SQL, Python, or internal-state
access with which to rediscover how a claim was established.

Audit contract `2.0` makes every material claim evidence-bearing: warnings and
limitations are typed `AuditObservation` objects with a `statement` and
`evidence_refs`, and `TableAudit` carries references for the profile it
asserts. `agents.audit_evidence.audit_claims` enumerates those claims with
positional, collision-free IDs — `audit:table:0`, `audit:table:0:warning:1`,
`audit:issue:0`, `audit:limitation:0` — so two claims cannot share an ID even
when a model repeats a table name or issue ID.

Both architectures now persist through one boundary,
`persist_audit_result`, which canonicalizes each claim against the ledger with
the same resolver that validates Lead output and refuses to persist a
non-blocked audit whose material claims have missing, failed, ambiguous, or
fabricated provenance. A blocked audit stays exempt so it is still reported
under its own blocked-audit condition rather than as a provenance failure. The
runner, the generalist persistence path, and the nested-auditor hook all route
through it, so an injected runner cannot bypass validation.

The Lead is handed a bounded typed `AuditEvidenceCatalog` under
`DATA_AUDIT_EVIDENCE_CATALOG_JSON` — one entry per resolving audit claim with
its canonical references, plus the flattened citable set. Claims that do not
resolve are omitted, so nothing in the catalog can be cited into an unsupported
answer, and a `claim_id` is explicitly a label rather than a reference. The
Lead gains no execution tool and no access to internal state. The section
heading `COMPLETED_DATA_AUDIT_JSON`, which is where the invented
`completed_data_audit` reference came from, is gone; a regression pins that the
production gate still rejects that exact citation.

`inspect_relations` now returns the `tool_event_id` of its persisted event.
Without it an auditor could establish a row count through a successful tool
call and still have nothing to cite for it. When no ledger is attached the
field stays null rather than advertising an unresolvable reference.

The contract change is versioned in three places: persisted state is written at
`CURRENT_STATE_SCHEMA_VERSION = "1.1"`, accepted alongside `legacy` and `1.0`;
`output_schema_fingerprint()` covers `AuditResult` and `GeneralistResult`, so
any existing pilot estimate is invalidated and a new manifest version is
required before paid execution; and contract `1.0` payloads still load with
their statements preserved and `evidence_refs` explicitly empty. Compatibility
keeps the 18 retained ledger states readable without inventing provenance for
them, so a legacy audit claim correctly reads as unsupported. Decision record
[0009](decisions/0009-audit-provenance-across-architectures.md) holds the
rationale; `tests/test_audit_provenance.py` holds the regressions.

### 15. Audit provenance enforced in capability and offline scoring (R21)

R20 stopped an unsupported audit from being persisted, but offline scoring is
the benchmark's source of truth and it runs against workspaces the current
runtime never touched. It previously accepted a completed `AuditResult` as the
data-audit capability and a matching issue ID as defect recall, with no check
that either resolved to anything executed. An audit could satisfy a scenario
requirement from failed SQL, a failed script, a deleted artifact, or an invented
reference.

`evaluation.primitives.resolve_audit_claims` now resolves every material audit
claim — the same positional claim IDs `schemas.audit.audit_claims` produces —
against the same successful-execution and verified-artifact boundary used for
findings, metrics, hypotheses, and statistical assessments. The projection moved
from `agents.audit_evidence` into `schemas.audit` so the evaluator resolves the
identical claims without importing anything that executes agents, and
`evaluate_workspace` resolves executed references once and passes that one set
to the audit, capability, and provenance checks so they cannot disagree.

Three scoring changes follow:

- `capability:data_audit` requires a completed audit that states at least one
  material claim and has no unsupported claim. A completed lifecycle status is
  no longer an analytical output on its own, and the message names the
  unsupported claim IDs.
- each required issue ID gains a `data_quality:required_provenance:{id}` check
  alongside the existing presence check, so an expected defect asserted from
  failed or fabricated evidence fails while the presence check still passes —
  making the distinction visible rather than silently scoring as recall.
- `data_quality:claim_provenance` covers every material claim, and
  `data_quality:clean_audit_evidence` requires a clean audit to demonstrate a
  performed check through a supported table profile or limitation. Reporting no
  defects is evidence of a clean dataset only when the checks behind it ran.

The clean-audit rule stays tool- and role-neutral, preserving R7 and R1: a
reference from `run_sql`, `run_python`, `inspect_relations`, or a verified
artifact all satisfy it equally, and no check inspects which agent produced the
audit. Architecture-equivalence fixtures assert that a five-role and a
single-role workspace holding the same audit produce byte-identical check
tuples, passing and failing together.

The catalog evaluator version advanced deliberately from `1.1` to `1.2`. One
explicit assertion in `tests/test_scenario_catalog.py` is the gate; the other
fixtures derive their evaluator version from the catalog so a future advance
needs one edit, not seven. `tests/test_audit_provenance_scoring.py` holds the
30 regressions.

### 16. One hypothesis-evidence rule, checked at the transition (R22)

Five places wrote or read hypothesis evidence and none of them stated the same
rule. The instructions said only to resolve a hypothesis "when the returned
evidence supports that disposition"; the `Hypothesis` contract said nothing at
all; `record_hypothesis` accepted any transition; final Lead validation
rejected unsupported resolutions, but only after the whole run had finished;
and offline evaluation applied its own inline status comparison. An invalid
resolution was therefore persisted into the ledger and its append-only history,
the model never learned it was invalid, and a resumed run could inherit it.

`schemas.hypotheses.hypothesis_requires_evidence` is now the single predicate
all four code paths call, so they cannot drift on which transitions need
provenance. The rule is one sentence: an open hypothesis may carry no evidence;
every supported, rejected, or inconclusive hypothesis must cite canonical
executed evidence.

The contract states it where the model can see it. Pydantic validators never
appear in a JSON schema, so the rule lives in the `status` and `evidence_refs`
field descriptions, which the strict output schema carries to the provider. The
Lead and Generalist instructions state it explicitly, including for qualitative
and data-quality hypotheses resolved from the audit — those cite the audit
claim's catalog references, never the audit itself.

`record_hypothesis` validates before the ledger is touched. A refused
resolution leaves the current hypothesis, the append-only history, the
`rejected_hypotheses` index, and the persisted file byte-identical, so a
resumed run reads the pre-transition state. The refusal is a typed
`invalid_hypothesis_transition` tool error carrying the hypothesis ID, the
requested status, which references failed to resolve, which resolved, a bounded
list of references that are actually available, and a remedy telling the model
to keep the hypothesis open rather than invent one. An accepted resolution is
persisted with its canonical references, so the intermediate state, the final
Lead result, and offline scoring read the same thing.

Open hypotheses stay usable and are left untouched: their references are not
canonicalized, because quietly dropping a reference the model still intends to
use would be the same silent rewrite this contract exists to prevent.

Offline evaluation now also checks the append-only history, not just the
current hypothesis list. Revising a claim must not erase that it was once
asserted without support, and the check catches unsupported transitions in
workspaces the current runtime never produced.

Because the `Hypothesis` field descriptions are part of the strict output
schema, `output_schema_fingerprint()` moved again — from
`de94152e…` to `4870a7fc…` — which correctly forces a new benchmark manifest
before paid execution. `tests/test_hypothesis_transitions.py` holds the 51
regressions.

### 17. One bounded correction for a semantic provenance failure (R23)

The failed canary returned well-formed JSON whose only defect was one citation.
That is not a malformed model response; it is a valid document making an
unsupported claim, and terminating the whole run over it produced no analytical
observation at the cost of every token the run had already spent. Rerunning from
the start would have been worse — resampling until a favourable output appears
is exactly what the provenance gate exists to prevent.

A strict-schema-valid response whose citations do not resolve now gets one
correction attempt, and the bound is structural rather than conventional:
`AgentRunConfig.evidence_correction_attempts` is validated `ge=0, le=1`, and the
correction agent runs with `max_turns=1`.

That agent has no tools at all — no SQL, no Python, no specialist delegation, no
Critic. It reuses the run's existing executions and spends no additional
resource budget; the deterministic regression asserts every budget counter and
the tool-event count are unchanged across a corrected run. Its only capability
is to re-emit the same typed result.

The request is specific rather than a blind retry. `LeadEvidenceError` now
carries typed `invalid_fields`, and the prompt contains those field IDs, the
validator's message, the previous output verbatim, and a bounded
`EvidenceCorrectionCatalog`: executed tool-event IDs and query/script paths,
persisted specialist findings with their canonical references, and the audit
evidence catalog from R20. Every entry derives from the run's own executed
evidence — no scenario ground truth, no evaluator rules, no orchestration
internals.

The application never edits a citation. The corrected response goes through the
identical validating persistence boundary that rejected the first one; if it
fails again, that failure is raised and the run ends. Both model calls stay
observable: the first response's rejection is a failed agent event carrying the
validation message, the correction is its own event with real start and
completion times, both bind to the active attempt, and usage from both
accumulates through the normal response-boundary accounting.

Two deliberate scope decisions are worth stating. First, the single-agent
baseline gets the same allowance through `run_generalist`. R23's wording names
the Lead, but giving one architecture a second attempt at valid provenance would
hand it an advantage the benchmark would then measure as an architecture
difference. Second, `AuditEvidenceError` stays terminal and is not corrected:
the audit is a preflight the rest of the run builds on, and R20 already refuses
to persist it unsupported.

The configured allowance is frozen into the manifest's
`run_configuration.parameters`, so it is covered by the declaration digest and
changing it forces a new manifest version. No output schema changed, so the
output-schema fingerprint is unchanged from R22. Decision record
[0011](decisions/0011-bounded-evidence-correction.md) holds the rationale and
`tests/test_evidence_correction.py` holds the 17 regressions.

### 18. One lossless citation-resolution contract (R24)

Provenance was judged by four implementations that had drifted apart. The Lead
kept a private copy of the resolver shadowing the shared one. The Critic checked
source lineage but never checked that a citation resolved at all. The runtime
asked whether *any* cited reference resolved while offline scoring asked whether
*all* of them did, so a workspace could pass at runtime and fail the evaluator
that scores the benchmark. The runtime exempted qualitative findings; offline
scoring did not. Offline scoring never applied the source-lineage rule the
runtime enforced.

Canonicalization was also lossy. A claim citing `[real_query,
completed_data_audit]` was rewritten to `[real_query]` and then passed an "any
resolves" test — one real query laundered an invented citation beside it, and
the invented one disappeared from the persisted record.

`agents/evidence.py` now owns the single contract. `resolve_citations` returns a
`CitationResolution` carrying what was cited, what resolved, and what did not;
`canonical_references` replaces resolved citations with the exact executed
references they stand for and preserves unresolved ones verbatim. A material
claim is supported only when every citation resolves — the `any(...)` test is
gone from every boundary. `material_claims` is the one definition of which
claims are held to the rule, and a regression asserts all four modules import
the identical function objects rather than equivalent copies.

Two rules the runtime already applied now also apply offline, because otherwise
the boundaries demonstrably disagree on the same persisted workspace:
qualitative findings must resolve like quantitative ones, and quantitative
claims must satisfy `has_source_lineage`. Open hypotheses stay exempt at both
boundaries and keep their citations untouched, per R22.

The runtime gate is therefore stricter than before. Aligning downward was not an
option — that would weaken provenance validation, which is the defect being
fixed — and R23's bounded correction exists to make the stricter gate
recoverable. Decision record
[0012](decisions/0012-single-citation-resolution-contract.md) holds the
rationale and `tests/test_citation_resolution.py` holds the 25 regressions.

### 19. Provenance failures are named, and the canary is retained (R25)

R18 gave every non-completion a typed reason. Semantic citation failures were
the gap it left: a run that ended because a well-formed answer cited evidence
that did not resolve fell through to `RunBlockReason.OTHER` and was published as
`FailureCategory.OTHER` — indistinguishable from a crash. That is the single
most likely failure mode of this system, and the 2026-08-20 canary is the proof;
a benchmark that cannot separate "the model made an unsupported claim" from
"something broke" cannot report reliability.

Every semantic citation failure now inherits
`agents.evidence.EvidenceProvenanceError`. Classification is by type, so a
provenance error added at a new boundary inherits the taxonomy instead of
landing in `other`, and no keyword matching is involved. The new reason is
checked before the generic `ModelBehaviorError` branch, because a response whose
citations do not resolve is a semantic failure of a well-formed answer, not a
malformed one — calling it a schema failure would misattribute it to the output
contract.

`AttemptRecord` gained a typed `block_reason`, and `finish_attempt` inherits the
run-level reason `mark_failed`/`mark_blocked`/`mark_cancelled` already set, so
attempt history carries the same taxonomy as the run state and the benchmark
record without every call site repeating it. A completed attempt carries no
reason at all. Regressions follow the category from attempt history through
benchmark records, aggregation, the report's table rows, and canonical offline
rescore including the persisted rescored document.

The 2026-08-20 failure is retained as a deterministic regression running the
real multi-agent lifecycle: an evidence-bearing audit produced through a real
`inspect_relations` call, and scripted Lead responses citing
`completed_data_audit`, with no provider call. Siblings pin that the same
handoff recovers when the R23 correction cites real evidence, and that the
original pre-R20 shape — an audit with no provenance at all — is now refused at
the audit boundary before the Lead ever runs.

Lifecycle fixtures that used empty audits now use evidence-bearing ones whose
claims cite an execution the workspace actually contains. An empty
`AuditResult` satisfies the provenance contract only because it claims nothing,
so a lifecycle test built on one stays green while the handoff it is supposed to
cover is broken — which is exactly what happened. `tests/conftest.py` provides
the shared builder. Decision record
[0013](decisions/0013-provenance-failure-taxonomy.md) holds the rationale;
`tests/test_provenance_failure_taxonomy.py` and `tests/test_failure_taxonomy.py`
hold the regressions.

## Verification completed

### Reopened R6 preflight and validity review, rerun at this revision

```text
508 passed, 16 deselected
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

The benchmark-validity review found and closed seven residual paths: pilot
retry after failure, overlapping or incomplete pilot partitions, manifests not
bound to the exact working tree, incorrect multi-agent blocked-audit taxonomy,
interrupt exits that could leave usage marked complete, incomplete usage being
accepted as pilot evidence, and continued pilot spending after a failed
stratum. Seven new regressions cover these cases.

The post-R19 R6 run remains open on the multi-agent live canary. After an initial
Docker-permission skip inside the restricted sandbox, the elevated
provider-backed run completed in 72.06 seconds with `1 failed, 2 passed`. The
single-agent canary and canary-coverage assertion passed. Multi-agent failed
with `LeadEvidenceError: lead outputs cite no executed evidence: hypothesis:H2`:
its supported `H2` cited `completed_data_audit`, which is neither a successful
tool execution nor a verified artifact. The retained ledger recorded 14 model
requests, 62,039 tokens, the successful audit and analyst events, three
successful SQL executions, and the failed Lead event.

The follow-up review established that this is not safely closed by a prompt-only
change or another opportunistic retry. R20–R25 must first preserve and validate
audit provenance end to end, align hypothesis transitions and citation
semantics, add one bounded correction path, and classify the failure explicitly.
The entire R6 preflight then starts again from a fresh revision.

### Deterministic verification at the R20 revision

```text
535 passed, 16 deselected
```

Ruff lint reported `All checks passed!` and format reported 150 files already
formatted. The 27 new `tests/test_audit_provenance.py` regressions cover claim
enumeration, missing/failed/ambiguous/fabricated/unrelated-success provenance,
verified and tampered artifact references, blocked-audit exemption, catalog
bounds and omission of unresolved claims, the `completed_data_audit` canary
regression, single-agent/multi-agent claim equivalence, and the three
versioning surfaces. The complete 10 × 2 × 3 declaration still produced 60
cells with 60 unique run IDs and 60 unique workspace paths, all 10 retained
benchmark manifests and 18 retained ledger states still load under audit
contract 2.0, and offline rescore and report still run against retained
benchmark evidence.

This is deterministic verification of R20 only. It is not the complete R6
preflight, which R25 reruns after R21–R24, and no live canary was run at this
revision.

### Deterministic verification at the R21 revision

```text
565 passed, 16 deselected
```

Ruff lint reported `All checks passed!` and format reported 152 files already
formatted. `tests/test_audit_provenance_scoring.py` adds 30 regressions:
capability outcomes for missing, incomplete, claimless, unsupported, and
supported audits; nine adversarial reference shapes for a required issue ID
(none, failed SQL event, failed query path, failed Python event, failed script
path, deleted artifact ID, missing artifact path, a fabricated reference, and a
file that never existed); the unrelated-success case, asserted against a run
that does hold three successful executions; tool- and role-neutral clean-audit
evidence; the artifact verification boundary before and after tampering;
architecture equivalence in both the passing and failing direction; and the
end-to-end engine wiring. Retained artifacts still load — 10 benchmark
manifests, 4 reports, and 18 ledger states — and report generation still runs
against retained benchmark evidence.

Two consequences of the deliberate `1.2` advance are worth stating plainly
rather than working around:

- offline rescore of the retained Task 10 manifests is now refused with
  `record ... does not match the selected evaluator rules` and exit status 2.
  Those records are bound to evaluator `1.1`, and rescoring them under `1.2`
  rules would be scoring old evidence with new rules. The refusal is the R8/R10
  identity binding behaving correctly; rescoring them still works when rules
  pinned to `1.1` are supplied explicitly.
- the retained Phase-1 canonical workspace, whose audit predates contract 2.0,
  now reports 7 offline failures instead of 4. The three new ones are
  `data_quality:clean_audit_evidence`, `data_quality:claim_provenance`, and
  `capability:data_audit`. That workspace already failed offline evaluation
  before this change, and the new failures are accurate: contract 1.0
  compatibility preserves its statements without inventing provenance for them.

This is deterministic verification of R21 only. The complete R6 preflight and
both live canaries remain R25's work.

### Deterministic verification at the R22 revision

```text
616 passed, 16 deselected
```

Ruff lint reported `All checks passed!` and format reported 154 files already
formatted. `tests/test_hypothesis_transitions.py` adds 51 regressions: the
shared predicate for all four statuses; the rule's presence in the model-visible
schema and in both agents' instructions; open hypotheses recorded without
evidence and with their references left untouched; every resolved status
(supported, rejected, inconclusive) crossed with direct event, direct path,
canonical alias, and local alias references; the same three statuses crossed
with empty, fabricated, failed-event, failed-path, missing-file, and
unknown-alias references; ambiguous aliases; the refusal leaving state, history,
the rejected index, and the persisted file unchanged; resume reading the
pre-transition state from disk; the actionable refusal payload and its bounded
suggestion list; a qualitative audit hypothesis resolved from the evidence
catalog; final Lead validation; and offline evaluation of both current
hypotheses and the append-only history.

Retained artifacts are unaffected — 10 manifests, 4 reports, and 18 ledger
states still load, and the retained Phase-1 canonical workspace still reports
the same 7 failures it had after R21, because its hypotheses and history all
resolve.

This is deterministic verification of R22 only. The complete R6 preflight and
both live canaries remain R25's work.

### Deterministic verification at the R23 revision

```text
633 passed, 16 deselected
```

Ruff lint reported `All checks passed!` and format reported 157 files already
formatted. `tests/test_evidence_correction.py` adds 17 regressions: the
allowance is one and cannot be configured higher; the correction agent has no
tools, no handoffs, and one turn; the prompt names the invalid field IDs and
carries the executed references, specialist findings, and audit claims; the
catalog is bounded and exposes no evaluator or internal state; a corrected
response succeeds after exactly two calls with no budget or tool-event change;
both calls and their usage are recorded against the active attempt; a second
invalid response raises the provenance error with nothing persisted; a zero
allowance skips correction entirely; a valid first response spends no extra
call; the correction is held to the same boundary, including a failed-execution
reference; the single-agent baseline behaves identically in both the succeeding
and terminating directions; and the whole thing works through the real
`AnalysisRunner` lifecycle, which re-validates the candidate a second time.

The 60-cell dry-run still produces 60 unique cells and now freezes
`evidence_correction_attempts: 1` in the manifest declaration. Retained
artifacts are unaffected — 10 manifests, 4 reports, and 18 ledger states still
load — and the output-schema fingerprint is unchanged, because R23 altered run
configuration rather than any output contract.

This is deterministic verification of R23 only. The complete R6 preflight and
both live canaries remain R25's work.

### Deterministic verification at the R24 revision

```text
658 passed, 16 deselected
```

Ruff lint reported `All checks passed!` and format reported 159 files already
formatted. `tests/test_citation_resolution.py` adds 25 regressions, the core of
which is a thirteen-shape matrix asserting that runtime validation, Critic
validation, and offline evaluation reach the *same* verdict on the same
workspace: direct event, direct path, canonical alias, and unique local alias
all supported; mixed valid/failed, mixed valid/fabricated, mixed
valid/ambiguous-alias, mixed valid/cyclic-alias, failed-only, fabricated-only,
ambiguous-alias-only, cyclic-alias-only, and unrelated-success-only all
unsupported. Every case runs against a workspace that does hold a successful
execution, so an unsupported verdict is never explained by an absence of
evidence. The suite also pins that unresolved citations survive
canonicalization, that aliases canonicalize deterministically without changing
any other field of the claim, that all four modules import the identical
resolver objects, and that hard-coded VALUES-only SQL now fails at runtime and
offline alike.

Two fixtures had to become production-shaped: the Task 6 calibration workspace
and the multi-agent lifecycle fixture previously executed SQL that read no
approved input relation, which the new offline lineage check correctly rejects.
The retained Phase-1 canonical workspace is unaffected — still the same 7
failures it had after R21 — and 10 manifests, 4 reports, and 18 ledger states
still load. The 60-cell dry-run is unchanged.

This is deterministic verification of R24 only. The complete R6 preflight and
both live canaries remain R25's work.

### Complete R6 preflight, closed after R20–R25

```text
678 passed, 3 Docker-permission skips, 17 live tests deselected
3 Docker-backed integrations passed with container access
4 final live-preflight tests passed in 92.86 seconds
```

That is 681 deterministic passes when the separately authorized Docker tests
are included. Ruff lint reported `All checks passed!` and format reported 163
files already formatted. Every declared preflight category was run as an
explicit selection rather than being inferred from the whole-suite result:

| Category | Result |
| --- | --- |
| Adversarial provenance suites (R20–R25) | 164 passed |
| Architecture-neutral evaluator and tool-mix fixtures | 24 passed |
| Workspace identity, evaluator errors, offline outputs | 23 passed |
| Lifecycle, interruption, attempt history | 48 passed |
| Failure taxonomy, outcome gate, pilot calibration, usage, pricing | 80 passed |
| Scenario-document integrity and catalog | 72 passed |
| Strict output contracts | 37 passed at the final revision |
| Docker-backed integrations | 3 passed, real containers |

The complete 10 × 2 × 3 declaration produced 60 cells with 60 unique run IDs and
60 unique workspace paths, and the R19 pilot set still partitions them by
architecture. Every retained `.runs/` artifact still loads — 10 benchmark
manifests, 4 reports, and 18 ledger states; every retained JSON artifact parses.

The first authorized live run failed both architectures because valid ISO-8601
JSON timestamps were revalidated with Python-object strictness after a
model-level legacy coercion validator. Replacing those model-level coercions
with field-level validators preserved legacy loading and restored the JSON
datetime contract. The next run passed single-agent but left multi-agent
blocked because orchestration marked every objective as requiring a chart; the
flag now derives from explicit visualization language in the objective. A
subsequent multi-agent run correctly refused an audit whose limitations had
empty provenance. The runtime boundary stayed unchanged and terminal, while
the provider-visible strict schemas for `AuditObservation`,
`DataQualityIssue`, and `TableAudit` now require at least one candidate
reference. The resolver still proves that every supplied reference is a
successful execution or verified artifact. These changes advanced
`output_schema_fingerprint()` to
`126195be58dea108393095556d38de2c90c316ebc1a6cda0664d21d866ac6bfc`, so no
historical pilot can authorize the next matrix.

After those failures were dispositioned, the final provider-backed suite passed
four tests in 92.86 seconds. The multi-agent canary completed with validation
`pass`, 18 requests, 84,300 tokens, 56.98 seconds, and known estimated cost
`$0.00767636`; its successful role trace included Data Auditor, Analyst, Lead,
and Critic. The single-agent canary completed with validation `pass`, 6
requests, 36,032 tokens, 21.53 seconds, and known estimated cost `$0.00371480`;
its trace contained only the Generalist. Both had one reconciled completed
attempt, no run error or block reason, and a persisted readable report. The
multi-agent trace retained one failed exploratory SQL event, but no claim used
it as evidence and the validated run completed; this is expected R2 evidence
isolation rather than a hidden retry or erased failure.

The final benchmark-validity review found no remaining code-level Task 10
blocker. R6 is closed. This does not publish a benchmark result: Task 10 must
still freeze a new clean-revision manifest, run the R19 stratified pilot, and
execute the declared matrix without changing rules mid-experiment. Decision record
[0014](decisions/0014-final-r6-live-contract-stabilization.md) preserves the
final live-contract changes.

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
| Operational reliability | 3 persisted pilot records failed; v2 workspace retained an interrupted attempt that its manifest omitted | Historical calibration evidence only; R16 now restores this class of missing operational record |
| Cost | v1 persisted `$0.00372068` but omitted the failed Lead response; v2 workspace retained `$0.0360329`; single-agent failed-call usage was lost | Accounting is incomplete; no valid pilot or full-run cost estimate |
| Latency | 120.38 s (v1), 71.98 s (v3), 61.79 s (gpt55) | Failed-pilot elapsed times only |
| Architecture comparison | No matched completed pair | Not estimable |

The repeated invalid-JSON outcome across models and architectures points to the
permissive output-schema integration, not simple API availability or one model
identity. No evaluator rule or score was changed to make an attempt pass, and
no result was selected because it favored either architecture. R13–R19 are
complete; R20–R25 and a fresh final R6 preflight must close before a new
manifest and pilot set are frozen under the same offline-evaluation rules.

## Diagnosed local execution issues

### Provider environment loading

The active VS Code agent process originally reported both variables absent,
including when checked through the project Python interpreter:

```text
OPENAI_API_KEY=absent
OPENAI_DEFAULT_MODEL=absent
```

The variables were subsequently confirmed in the repository's ignored `.env`
file without printing their values. The 2026-08-20 live rerun loaded them with
`uv run --env-file .env`; provider access succeeded. Future bounded live commands
should use the same explicit loading path. As an alternative, exporting
variables in VS Code's integrated terminal affects only that shell and processes
launched by it; it does not update an extension host that is already running.
The safe inherited-environment setup is:

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
2. R20–R25 are implemented: audit contract 2.0 with one validating persistence
   boundary, the Lead's bounded `AuditEvidenceCatalog`, persisted-state version
   `1.1`, offline enforcement at catalog evaluator version `1.2`, one shared
   hypothesis-evidence rule enforced at the transition, one bounded tool-less
   correction attempt available to both architectures, one lossless
   citation-resolution contract, and a named provenance failure taxonomy with
   the 2026-08-20 canary retained as a deterministic regression. Their
   rationale is in decision records 0009 through 0013.
3. The complete R6 preflight is closed at this revision — 681 deterministic
   tests including real Docker containers, Ruff, every declared category as an
   explicit selection, all 60 dry-run cells, every retained artifact, and both
   paid live architecture canaries under the R17 outcome gate. Every observed
   live failure was dispositioned before retry.
4. Recheck variable presence without printing the key and confirm Docker access.
5. Select the compatible model and freeze a new clean-revision manifest before
   any paid execution. The final output-schema fingerprint invalidates every
   historical pilot.
6. Review the declared ten-scenario × two-architecture × three-repetition matrix,
   budgets, evaluator versions, code revision, and output paths.
7. Run and retain the R19 pilot set, including at least one cell per architecture.
8. Decide whether the declared repetition count remains affordable. If it must
   change, create a new manifest/version and record the justification before the
   full run.
9. Resume the immutable matrix with `--allow-paid`; do not use `--force` or
    overwrite workspaces.
10. Evaluate every persisted workspace offline using the frozen evaluator rules.
11. Inspect failures without changing rules mid-experiment. If code or evaluator
    changes are required, version the benchmark and rerun the affected declared
    matrix rather than silently patching scores.
12. Generate and retain raw manifests, run records, evaluator results, pilot
    report, and aggregate report.
13. Publish real results and limitations, including denominators, failed runs,
    sample size, model specificity, evaluator limitations, uncertainty, cost,
    and latency—regardless of which architecture performs better.
