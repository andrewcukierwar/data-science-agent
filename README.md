# Data Science Agent

Foundation for an evidence-backed, multi-agent business analytics system.

Phase 0 deterministic infrastructure and the Phase 1 multi-agent MVP are
complete. Phase 2 Tasks 1–9 and remediations R1–R12 are implemented and
deterministically verified. Task 10—the paid single-agent versus five-agent
benchmark—has not yet run, so this repository does not claim benchmark results.
See
[`docs/phase2-status.md`](docs/phase2-status.md) for the implementation ledger,
verification record, and live-run handoff.

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
deterministic verification completed with **369 passed and 13 live tests
deselected**; Ruff lint and formatting checks passed, and the 10 × 2 × 3 matrix
dry-run produced 60 unique cells.

Before Task 10, review the twelve documented remediation tasks in
[`PROJECT_PLAN.md`](PROJECT_PLAN.md): architecture-neutral evaluation (R1),
hardened evidence provenance (R2), scenario-bound workspaces (R3), explicit
evaluator errors (R4), hardened benchmark execution semantics (R5), and
scenario-document integrity plus full deterministic preflight (R6). The
follow-up review adds capability-driven tool neutrality (R7), universal
workspace binding (R8), canonical aggregation-safe rescoring (R9), pilot/run
binding (R10), durable attempt history (R11), and non-destructive offline
outputs (R12).

R1–R12 are implemented and covered by architecture-equivalence, capability/tool-
mix, failed-evidence, workspace identity, evaluator-error, lifecycle,
aggregation-safe rescore, pilot/run-record binding, append-only attempt
reconciliation, scenario-document integrity, and exclusive atomic offline-output
fixtures. The final R6 preflight passed, including all Docker-backed integration
tests and all 60 declared dry-run cells. The catalog evaluator version is now
`1.1` for these scoring changes.

No Phase 2 experiment manifest has been frozen and no paid matrix cells have
been executed. Existing canonical MVP workspaces predate the declared Phase 2
matrix and must not be presented as its results. Freeze a fresh manifest and
obtain explicit paid-execution authorization before starting Task 10.

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

Run one paid cost-estimation cell, then resume the remaining immutable cells:

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
