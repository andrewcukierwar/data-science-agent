# Data Science Agent

Foundation for an evidence-backed, multi-agent business analytics system.

Phase 0 deterministic infrastructure and the Phase 1 multi-agent MVP are
complete. Phase 2 Tasks 1–9 and remediations R1–R12 are implemented. Task
10—the paid single-agent versus five-agent benchmark—was attempted on
2026-08-19, but the retained pilots exposed additional release blockers tracked
as R13–R19. R13 (strict analytical output schemas, including both live
architecture canaries), R14 (failure-safe usage and cost accounting), R15
(single-agent attempt lifecycle), R16 (retained and resumable interrupted
cells), R17 (outcome-sensitive preflight gate), R18 (explicit block reasons and
accurate failure taxonomy), and R19 (stratified pilot-set calibration) are all
complete. The deterministic R6 rerun and benchmark-validity review are also
complete. On 2026-08-20 the fresh single-agent canary passed, but the
multi-agent canary failed its executed-evidence gate. Remediations R20–R25 now
track the resulting audit/Lead provenance gaps, and R20–R25 are now all
complete. The complete deterministic R6 preflight has been rerun; the gate
remains open only on its two paid live architecture canaries, which have not
been run. The paid matrix remains blocked, the final R6 gate is
open, and no complete benchmark result is claimed. See
[`docs/phase2-status.md`](docs/phase2-status.md) for the implementation ledger,
verification record, retained run artifacts, and the blocked live-run report.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

The planned architecture and implementation sequence are documented in
[`PROJECT_PLAN.md`](PROJECT_PLAN.md).

Implementation decisions and the Phase 1 hardening handoff are recorded in
[`docs/decisions/`](docs/decisions/README.md) and
[`docs/phase1-lessons.md`](docs/phase1-lessons.md).

## Phase 2 status

The repository now contains versioned evaluation contracts, a zero-API offline
evaluation engine, ten deterministic scenarios, calibrated correct and
adversarial fixtures, a bounded generalist baseline, an immutable resumable
benchmark runner, and deterministic aggregation/reporting. The latest full
deterministic verification completed with **673 passed and 16 live tests
deselected**; Ruff lint and formatting checks passed, and the 10 × 2 × 3 matrix
dry-run produced 60 unique cells. That is deterministic verification only; the
complete R6 preflight, including both paid live canaries, is still open.

Before Task 10, review the twenty-five documented remediation tasks in
[`PROJECT_PLAN.md`](PROJECT_PLAN.md): architecture-neutral evaluation (R1),
hardened evidence provenance (R2), scenario-bound workspaces (R3), explicit
evaluator errors (R4), hardened benchmark execution semantics (R5), and
scenario-document integrity plus full deterministic preflight (R6). The
follow-up review adds capability-driven tool neutrality (R7), universal
workspace binding (R8), canonical aggregation-safe rescoring (R9), pilot/run
binding (R10), durable attempt history (R11), and non-destructive offline
outputs (R12).

The post-pilot review adds strict analytical output schemas (R13, complete),
failure-safe usage and cost accounting (R14, complete), complete single-agent
attempt lifecycle (R15, complete), interrupted-cell retention and resume (R16,
complete), outcome-sensitive preflight tests (R17, complete), accurate
blocked-run taxonomy (R18, complete), and representative pilot calibration
across architectures/workload classes (R19, complete).

The live-canary review adds six provenance remediations: typed audit
provenance across architecture boundaries (R20, P0, complete),
audit-provenance enforcement in capability and offline scoring (R21, P0,
complete), aligned hypothesis evidence transitions (R22, P1, complete), one
bounded semantic correction cycle (R23, P1, complete), lossless and consistent
citation resolution (R24, P1, complete), and explicit provenance-failure
taxonomy plus final regression closure (R25, P2, complete apart from the paid
live canaries).

R13 replaces every open-ended dimension map with a typed `MetricDimension`
list, so all six production agent output types compile through the Agents SDK
strict-schema converter with no `strict_json_schema=False` opt-out. Malformed,
truncated, or extra-field model output now raises an explicit
`AgentOutputContractError` instead of being re-parsed permissively. The two
opt-in live canaries — one per architecture — passed on 2026-08-19, the first
live evidence that the contract holds against a real provider. They must be
rerun inside the reopened R6 preflight after R19.

R14 records model usage as each provider response arrives and reconciles it once
per run on both the success and the failure path, so an invalid-JSON final
output, a turn-limit failure, or a later lifecycle error keeps the tokens the
provider already reported. Usage that cannot be reconciled is marked incomplete
and its cost is published as unavailable, so incomplete usage can no longer
appear as a known `$0.00`.

R15 brings the single-agent runner under the same append-only attempt protocol
as the multi-agent runner. One attempt opens before agent execution and closes
as completed, blocked, failed, or interrupted with matching timing, usage, cost
availability, and error; resume appends a new attempt without rewriting prior
records. Both architectures now publish the same attempt identity and history in
benchmark records.

R16 retains an interrupted cell as a cancelled operational record — written
before the manifest is marked aborted — with its workspace, attempt history,
partial usage, cost availability, latency, and interruption reason. Aggregate
denominators count it as an observed operational failure instead of a missing
repetition, and an explicit resume retries only interrupted cells, appending a
new attempt while leaving prior attempt evidence unchanged.

R17 replaces presence-only smoke assertions with one shared outcome gate:
completion, a readable persisted report, usage that is nonzero or explicitly
unavailable, explicit cost, and a reconciled attempt history with no attempt
left running. The live smoke tests, both live canaries, and deterministic
failure fixtures all use the same gate, and it rejects all four retained Task 10
pilot workspaces. The deterministic post-R19 R6 rerun now passes; the shared
gate still awaits fresh provider-backed canary evidence.

R18 persists a typed block reason and a readable detail for every
non-completion, so the benchmark reads the originating condition instead of
hard-coding every blocked run as a budget failure. Only genuine run-budget
exhaustion is categorized as budget; an unresolved self-critique, an unresolved
follow-up, a schema violation, an agent turn limit, a blocking data-quality
audit, and an interruption each get their own category. Blocked and cancelled
runs stay operational observations rather than analytical evaluator failures.

R19 replaces the single first-cell extrapolation with a declared pilot set: one
stratum per architecture (plus optional named workload strata), one measured
cell per stratum, and a stratified estimate with an explicit range and a named
scaling method. Every per-pilot observation is retained and bound to its
immutable run record, and manifest-declaration and output-schema digests force a
new manifest version on any model, turn-budget, matrix-size, pilot-set, or
schema change.

R20 gives the Data Audit the provenance the rest of the evidence contract
already required. Audit contract `2.0` replaces provenance-free warning and
limitation strings with typed `AuditObservation` objects and gives each table
profile its own `evidence_refs`. Both architectures persist through one
boundary that canonicalizes every claim against the ledger and refuses a
non-blocked audit whose material claims have missing, failed, ambiguous, or
fabricated provenance. The Lead then receives a bounded typed
`AuditEvidenceCatalog` — one entry per resolving claim with its exact executed
references — instead of raw audit JSON under a heading that read like a
citation, so it never needs a pseudo-reference such as `completed_data_audit`
and still gains no SQL, Python, or internal-state access. Persisted state is
versioned `1.1`, contract `1.0` payloads still load with their provenance
explicitly empty, and the output-schema fingerprint change forces a new
benchmark manifest before any paid execution.

R21 carries that boundary into offline scoring, which is the benchmark's source
of truth and runs against workspaces the current runtime never touched. A
completed `AuditResult` no longer satisfies the data-audit capability by itself:
the audit must state a material claim and every material claim must resolve to
successful execution or a verified artifact. Each required issue ID is scored
for presence and for provenance separately, so an expected defect asserted from
failed SQL, a failed script, a deleted artifact, a missing file, or an invented
reference fails rather than scoring as recall. A clean audit must show a
performed check through a supported table profile or limitation — reporting no
defects is evidence of a clean dataset only when the checks behind it ran — and
that rule stays neutral about which tool or role produced the evidence. The
catalog evaluator version advanced deliberately to `1.2`, so records scored
under `1.1` are refused rather than silently rescored under the new rules.

R22 makes one rule govern hypothesis evidence everywhere it is written or read:
an open hypothesis may carry no evidence, and every supported, rejected, or
inconclusive hypothesis must cite canonical executed evidence. One shared
predicate backs the `Hypothesis` contract, the `record_hypothesis` state tool,
final Lead validation, and offline evaluation, so they cannot drift. The rule is
stated in the field descriptions the strict output schema carries to the model
and in both agents' instructions, including for qualitative hypotheses resolved
from the data audit. `record_hypothesis` now refuses an unsupported resolution
*before* touching the ledger — leaving the current hypothesis, the append-only
history, and the persisted file unchanged so a resumed run cannot inherit it —
and returns a typed error naming the unresolved references and the references
that are actually available. Offline evaluation also checks the append-only
history, so revising a claim cannot erase that it was once asserted without
support.

R23 makes a strict-schema-valid response whose citations do not resolve
recoverable exactly once. That response is not malformed — it is a valid
document with a fixable citation — so terminating the whole run over it wastes
every token already spent, while rerunning from the start would be resampling
until a favourable output appears. The correction agent has no tools at all, so
it reuses the run's existing executions and spends no SQL, Python, specialist,
or Critic budget; the allowance is validated `ge=0, le=1` and the agent runs for
one turn, so it cannot become a retry loop. The request names the exact output
fields that failed and carries a bounded catalog of every reference the run can
legitimately cite. The corrected response passes the identical validation
boundary that rejected the first one — the application never edits a citation —
and a second invalid response ends the run with the provenance failure. Both
model calls, their usage, and their outcomes are recorded against the active
attempt. The single-agent baseline gets the same allowance, because giving it to
only one architecture would be measured as an architecture difference.

R24 replaces four drifted provenance implementations with one contract. The
Lead's private copy of the resolver is gone, the Critic now checks that a
citation resolves at all, and resolution is lossless: it reports what resolved
*and* what did not, so a fabricated reference can no longer disappear because a
real one sat beside it. A material claim is supported only when every citation
resolves — one real query no longer launders an invented citation next to it.
Runtime validation, Critic validation, offline evaluation, and offline rescoring
now reach the same verdict on the same persisted workspace, including for
qualitative findings and source-lineage, which offline scoring previously
skipped. The runtime gate is stricter as a result; aligning downward would have
weakened provenance validation, and R23's bounded correction makes the stricter
gate recoverable.

R25 gives semantic citation failures a name. Every provenance error inherits one
base class, so classification is by type rather than by keyword, and a run that
ended because a well-formed answer cited evidence that did not resolve is now
recorded as `evidence_provenance` instead of `other` — indistinguishable from a
crash. The category propagates through attempt history, benchmark records,
aggregation, failure reports, and canonical offline rescore. The 2026-08-20
canary is retained as a deterministic regression over the real multi-agent
lifecycle with no provider call, alongside siblings pinning that the same
handoff recovers when the correction cites real evidence and that the
provenance-free audit is refused before the Lead runs. Lifecycle fixtures now
use evidence-bearing audits: an empty `AuditResult` passes the contract only
because it claims nothing, which is how those fixtures stayed green while the
handoff was broken.

R1–R12 are implemented and covered by architecture-equivalence, capability/tool-
mix, failed-evidence, workspace identity, evaluator-error, lifecycle,
aggregation-safe rescore, pilot/run-record binding, append-only attempt
reconciliation, scenario-document integrity, and exclusive atomic offline-output
fixtures. The catalog evaluator version is now `1.2`, advanced for the R9
aggregation changes and again for R21 audit-provenance scoring.
The reopened R6 preflight has been rerun at this revision: the 508-test
deterministic suite, Ruff, every declared adversarial suite, the Docker-backed
integration tests, all 60 declared dry-run cells, and a benchmark-validity code
review pass. That review closed pilot selection, pilot-partition, repository
identity, failure-taxonomy, and incomplete-accounting gaps. A fresh paid run on
2026-08-20 passed the single-agent canary and coverage assertion but failed the
multi-agent canary because Lead hypothesis `H2` cited no executed evidence.
The complete deterministic R6 preflight has been rerun at this revision. Both
paid live architecture canaries must still pass before another Task 10 manifest
is frozen.

Task 10 execution is currently blocked before the paid matrix. Four immutable
attempts were retained under `.runs/phase2-task10-20260819/`: three attempts
with `gpt-5.6-luna` (two invalid structured-output responses and one interrupted
partial multi-agent attempt) and one `gpt-5.5` attempt (invalid structured-output
response). The first manifest retained 28,825 accounted tokens and
**$0.00372068** over **120.38 seconds**, but those totals omit the failed Lead
response and are not a trustworthy complete cost. Failed single-agent calls
lost their usage entirely, and the interrupted v2 cell was omitted from its
manifest despite retained workspace evidence. The corresponding manifests,
pilot reports, workspaces, offline-rescored manifests, and aggregate reports
are retained.
Because no pilot completed, the full 60-cell matrix was not started. Existing
canonical MVP workspaces predate the declared Phase 2 matrix and are not
substitutes for its results. Fresh R6 live canaries must pass, and then a new
manifest version and pilot set must be frozen.

### Task 10 execution record (blocked)

The attempted manifests and reports are local evidence, not benchmark results.
The runner's `multi-agent` label denotes the planned five-agent architecture.

| Manifest | Model | Persisted observations | Outcome |
| --- | --- | ---: | --- |
| `phase2-task10-20260819-luna-v1` | `gpt-5.6-luna` | 1/60 (multi-agent) | Invalid JSON; persisted usage/cost omit the failed Lead response |
| `phase2-task10-20260819-luna-v2` | `gpt-5.6-luna` | 0/60 | Interrupted after partial progress; workspace evidence retained but manifest record dropped |
| `phase2-task10-20260819-luna-v3` | `gpt-5.6-luna` | 1/60 (single-agent) | Invalid JSON after 71.98 s; failed-call usage lost and cost incorrectly recorded as known zero |
| `phase2-task10-20260819-gpt55-v1` | `gpt-5.5` | 1/60 (single-agent) | Invalid JSON after 61.79 s; failed-call usage lost and pricing unavailable |

The three failed records were rescored offline after the R9 lifecycle fix and
are explicitly `not_evaluated` with no analytical score. Their reports show
59 missing cells each; the aborted v2 report shows all 60 cells missing. Thus
there are no observed task-success, numerical-accuracy, unsupported-claim, or
architecture-comparison values to publish. The observed operational outcome is
one failed pilot cell for each non-aborted attempt, not a claim about full-run
architecture reliability. Direct minimal API availability checks do not repair
the agent structured-output failure, and no result was selected or invented to
favor either architecture.

## Canonical Phase 1 live acceptance

The full no-steering canonical run requires Docker, `OPENAI_API_KEY`, and
`OPENAI_DEFAULT_MODEL`:

```bash
OPENAI_API_KEY=... OPENAI_DEFAULT_MODEL=... uv run python scripts/run_canonical_mvp.py
```

It writes the isolated workspace under `.runs/canonical-mvp/canonical-q2-mvp`,
prints a persisted-workspace acceptance summary, and uses evaluator-only
scenario ground truth after the agent run. Use `--force` only to intentionally
replace that exact run directory.

## Generic offline evaluation

Evaluate a persisted workspace without loading agents, `.env`, or making API
calls:

```bash
uv run python scripts/evaluate_workspace.py \
  .runs/canonical-mvp/canonical-q2-mvp \
  --legacy-diagnostic \
  --scenario-id canonical-q2-profitability \
  --scenario-version 1.0
```

The standalone evaluator derives scenario rules from a bound workspace identity
and refuses explicit rule mismatches. An unbound legacy workspace requires
`--legacy-diagnostic` plus explicit `--scenario-id` and `--scenario-version`;
that path is diagnostic-only and cannot enter a benchmark manifest.

For benchmark manifests, use the benchmark runner's rescore path, which refuses
to overwrite the input manifest:

```bash
uv run python scripts/run_benchmark.py offline-rescore benchmark-manifest.json \
  --output benchmark-rescored.json
```

The legacy `scripts/evaluate_manifest.py` path delegates to the same canonical
rescorer and uses the same exclusive atomic output handling.

## Resumable benchmark matrix

Plan the first benchmark with three repetitions per scenario and architecture.
Planning writes the manifest before any workspace or agent execution:

```bash
uv run python scripts/run_benchmark.py plan benchmark.json --model gpt-5.6-luna
```

The paid commands below are the current CLI surface, but **must not be run again
until both paid live architecture canaries pass and the reopened R6 gate
closes**. R19 already
replaced the single first-cell estimate with a declared pilot set containing at
least one cell per architecture before the remaining immutable cells can resume:

```bash
uv run python scripts/run_benchmark.py pilot benchmark.json --allow-paid
uv run python scripts/run_benchmark.py run benchmark.json --allow-paid
```

The full-run gate verifies the pilot against its manifest-bound run-record
digest, model/configuration identity, usage, latency, pricing, and cost. If the
pilot cannot resolve pricing for the declared model, the full run is blocked
until that uncertainty is explicitly acknowledged with `--unknown-cost`; the
acknowledgement is persisted against that exact pilot record.

Live execution requires `--allow-paid`, `OPENAI_API_KEY`, and a matching
`OPENAI_DEFAULT_MODEL`. The runner never loads `.env`, never overwrites an
existing run workspace, and records provider/operational failures separately
from analytical evaluator results. Use `dry-run` to inspect a matrix without
writing it, or `offline-rescore` to produce a new manifest after evaluator
changes without rerunning agents:

```bash
uv run python scripts/run_benchmark.py dry-run \
  --scenario-id canonical-q2-profitability --model gpt-5.6-luna
uv run python scripts/run_benchmark.py offline-rescore benchmark.json \
  --output benchmark-rescored.json

# Generate deterministic README-ready rows and architecture comparisons
uv run python scripts/run_benchmark.py report benchmark.json \
  --output benchmark-report.json
```

Benchmark reports retain raw records in the source manifest and expose
per-scenario/architecture denominators, completion and evaluation rates, score
distributions, Student-t intervals when sample size permits, cost/latency
summaries, failure taxonomy, and paired architecture differences. A paired
result is labeled `supported_difference`, `not_supported`, or
`insufficient_sample`; descriptive means are never presented as proof of an
architecture advantage.

### Credentials when using the VS Code extension

An `export` entered in VS Code's integrated terminal changes only that terminal
and its child processes. It does not update an already-running VS Code extension
host. Fully quit VS Code, then launch the repository from a macOS terminal that
already has the variables:

```bash
export OPENAI_API_KEY="<your key>"
export OPENAI_DEFAULT_MODEL="<the exact model frozen in the manifest>"
code /path/to/data-science-agent
```

Install the `code` launcher with **Shell Command: Install 'code' command in
PATH** from the VS Code Command Palette if needed. Start a new agent conversation
after reopening VS Code. Verify the key without displaying it:

```bash
test -n "$OPENAI_API_KEY" && echo "API key is set"
printf '%s\n' "$OPENAI_DEFAULT_MODEL"
```

Never paste the key into chat, write it into a benchmark manifest, or commit it.
The manifest model and `OPENAI_DEFAULT_MODEL` must match exactly.

Docker Desktop is healthy on the development machine. A Docker permission error
from this agent was caused by the restricted workspace sandbox blocking the
Docker Unix socket; the same read-only `docker info` check succeeded with
explicit elevated permission. This is not a repository or socket-ownership
failure.

## Versioned scenario catalog

Scenario generation and evaluator rules resolve through the versioned catalog:

```python
from scenarios import get_scenario

scenario = get_scenario("canonical-q2-profitability", "1.0")
run = scenario.generate_validated()
```

The built-in catalog also includes `retention-q2-deterioration`,
`cogs-q2-margin-deterioration`, and `discount-refund-q2-deterioration`, each at
version `1.0` with its own deterministic generator, evaluator, and invariant
suite. It also includes `missing-reporting-day`,
`partial-latest-reporting-day`, `meaningful-ab-treatment-effect`,
`no-effect-ab-experiment`, and `significant-but-immaterial-ab-effect`, each at
version `1.0` with deterministic source and evaluator contracts. The catalog
also includes `channel-mix-confounding` for attribution-mix and causal-claim
calibration.

The clean synthetic ecommerce baseline remains independently available through
`SyntheticEcommerceGenerator` and `validate_synthetic_ecommerce_baseline`.
