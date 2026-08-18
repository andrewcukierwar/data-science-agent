# Data Science Agent

Foundation for an evidence-backed, multi-agent business analytics system.

Phase 0 deterministic infrastructure and the Phase 1 multi-agent MVP are
complete. Phase 2: Evaluation and Reliability is underway, expanding the
deterministic scenario suite and benchmarking the five-agent system against a
single-agent baseline.

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
  .runs/canonical-mvp/canonical-q2-mvp
```

Rescore all persisted workspaces referenced by a manifest. The input manifest
is never overwritten:

```bash
uv run python scripts/evaluate_manifest.py benchmark-manifest.json \
  --output benchmark-rescored.json
```

## Resumable benchmark matrix

Plan the first benchmark with three repetitions per scenario and architecture.
Planning writes the manifest before any workspace or agent execution:

```bash
uv run python scripts/run_benchmark.py plan benchmark.json
```

Run one paid cost-estimation cell, then resume the remaining immutable cells:

```bash
uv run python scripts/run_benchmark.py pilot benchmark.json --allow-paid
uv run python scripts/run_benchmark.py run benchmark.json --allow-paid
```

Live execution requires `--allow-paid`, `OPENAI_API_KEY`, and a matching
`OPENAI_DEFAULT_MODEL`. The runner never loads `.env`, never overwrites an
existing run workspace, and records provider/operational failures separately
from analytical evaluator results. Use `dry-run` to inspect a matrix without
writing it, or `offline-rescore` to produce a new manifest after evaluator
changes without rerunning agents:

```bash
uv run python scripts/run_benchmark.py dry-run --scenario-id canonical-q2-profitability
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
