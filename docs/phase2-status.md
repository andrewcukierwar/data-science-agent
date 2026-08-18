# Phase 2 implementation status and benchmark handoff

**Status date:** 2026-08-18  
**Implementation:** Tasks 1–9 complete

**Remediation:** R2 verified; R1/R3/R4/R5 partial; R6–R12 pending before Task 10

**Experiment:** Task 10 not started; no results published

This document records the Phase 2 work completed before any paid benchmark, the
required pre-benchmark remediation, and the exact boundary between tested
infrastructure and empirical results. It is a handoff record, not a benchmark
report.

## Phase 2 Pre-Benchmark Remediation: R1–R12

The following remediation tasks must be completed and deterministically
verified before Task 10 begins:

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
6. **R6 — Fix scenario-document integrity + full preflight.** Remove the false
   model-visible clean/no-injection assertion, test that injected scenarios do
   not retain baseline-only assertions, and run the complete deterministic
   preflight: tests, Ruff, architecture-neutral fixtures, workspace mismatch,
   failed evidence, evaluator exceptions, interrupted resume, unknown pricing,
   all 10 × 2 × 3 dry-run cells, and a benchmark-validity-focused Sol High code
   review. This is the final gate and must be rerun after R7–R12.
7. **R7 — Make tool use capability-driven [P0].** Remove unconditional SQL and
   Python presence gates. Express requirements as scenario-specific typed
   capabilities and test equivalent outputs across different valid tool mixes.
8. **R8 — Enforce identity at every offline boundary [P1].** Match persisted
   workspace identity to selected rules and complete manifest identity for
   standalone, completed, and non-completed rescore paths. Keep unbound legacy
   evaluation explicitly outside benchmark use.
9. **R9 — Consolidate aggregation-safe rescoring [P1].** Route APIs and CLIs
   through one per-record error-isolated implementation, then recompute
   aggregates and paired comparisons from the rescored records.
10. **R10 — Bind the pilot to its run record [P2].** Verify a canonical run-record
    digest and re-derive usage, latency, pricing availability, and cost before
    permitting the remaining matrix.
11. **R11 — Persist append-only attempt history [P2].** Retain every attempt ID,
    timing, outcome, usage/cost delta, and event attribution; reconcile those
    records to cumulative benchmark totals.
12. **R12 — Make offline outputs non-destructive and atomic [P2].** Refuse input
    paths and existing outputs, consolidate exclusive writes, and retire or
    delegate unsafe legacy CLI behavior.

### Follow-up review disposition

| Remediation | Current disposition | Required closure |
| --- | --- | --- |
| R1 | Partial | R7 capability/tool-mix regressions |
| R2 | Verified | Retain all four failed-evidence adversarial fixtures |
| R3 | Partial | R8 enforcement across every offline entry point |
| R4 | Partial | R9 canonical per-record error isolation and reaggregation |
| R5 | Partial | R10 pilot binding and R11 durable attempt history |
| R6 | Pending | Remove false documents and rerun the final preflight after R7–R12 |

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

`scripts/evaluate_workspace.py` evaluates one persisted workspace and
`scripts/evaluate_manifest.py` evaluates a manifest. These paths do not load
`.env`, invoke agents, or make API calls. Until R8, R9, and R12 are complete,
they are diagnostic interfaces rather than approved benchmark-rescore paths;
benchmark manifests should use `scripts/run_benchmark.py offline-rescore`.
Stable serialization supports
byte-for-byte repeatability where timestamps or equivalent semantics do not
require normalization. The final report, Critic, and evaluator use the same
compiled final metric set.

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

## Verification completed

The latest full deterministic review run completed with:

```text
330 passed, 13 deselected
```

The deselected tests are opt-in live tests. Ruff lint and format checks also
passed, and the complete 10 × 2 × 3 declaration produced 60 unique cells and
workspace paths. Deterministic fake-run coverage includes resume, interruption,
duplicate IDs, failed cells, immutable workspaces, paid-execution guards, pilot
enforcement, and offline rescoring. The follow-up review also demonstrated that
the green suite does not yet cover tool-mix neutrality, standalone scenario
identity mismatch, tampered pilot cost, or non-empty legacy batch rescoring.

## Task 10 is intentionally blocked pending R1–R12

No Phase 2 benchmark manifest currently exists. No paid pilot or single-agent
versus five-agent matrix has been executed, evaluated, or aggregated. Existing
canonical MVP workspaces were created under the earlier acceptance workflow and
are not valid substitutes for declared Phase 2 cells. Task 10 must not begin
until R1–R12 are complete and the full deterministic preflight is green.

Therefore there are currently no honest Phase 2 values for task success,
numerical accuracy, unsupported claims, operational reliability, cost, latency,
or architecture differences. Values must remain unpublished rather than be
inferred, invented, or selected from unrelated runs.

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

After reopening VS Code with inherited credentials:

1. Recheck variable presence without printing the key and confirm Docker access.
2. Select the real model and freeze a new manifest before any paid execution.
3. Review the declared ten-scenario × two-architecture × three-repetition matrix,
   budgets, evaluator versions, code revision, and output paths.
4. Run and retain the one-cell cost-estimation pilot.
5. Decide whether the declared repetition count remains affordable. If it must
   change, create a new manifest/version and record the justification before the
   full run.
6. Resume the immutable matrix with `--allow-paid`; do not use `--force` or
   overwrite workspaces.
7. Evaluate every persisted workspace offline using the frozen evaluator rules.
8. Inspect failures without changing rules mid-experiment. If code or evaluator
   changes are required, version the benchmark and rerun the affected declared
   matrix rather than silently patching scores.
9. Generate and retain raw manifests, run records, evaluator results, pilot
   report, and aggregate report.
10. Publish real results and limitations, including denominators, failed runs,
    sample size, model specificity, evaluator limitations, uncertainty, cost,
    and latency—regardless of which architecture performs better.
