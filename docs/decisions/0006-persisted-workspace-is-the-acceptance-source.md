# 0006: Persisted workspace is the acceptance source

- Status: Accepted
- Date: 2026-08-17
- Phase: Phase 1

## Context

Canonical acceptance originally depended on an in-memory `AnalysisRunResult`.
That made a completed run difficult to evaluate after the live process exited
and coupled benchmark semantics to transient orchestration state. It also
treated multiple compatible metric observations as an error.

## Decision

The persisted workspace is the reproducible acceptance boundary:

- load `state/analysis_ledger.json` and the final report from the workspace;
- evaluate lifecycle state, audit, traces, budgets, provenance, artifacts,
  validation, report, and structured metrics without executing agents;
- use the same core acceptance logic for live post-run evaluation and offline
  reevaluation;
- make zero OpenAI API calls during offline evaluation.

Canonical metric matching normalizes key, dimensions, periods, comparison type,
and unit. It prefers an observation whose dimensions exactly equal expected
evaluator dimensions. If none exists, compatible dimension supersets may be
used. Multiple compatible candidates are reconciled: consistent values are
corroborating evidence; materially conflicting values fail. Numeric tolerances
are not loosened.

Semantic root-cause checks require an asserted evidence-backed mechanism. A
sentence that merely says a metric “may be worth investigating” does not count
as explaining the acquisition deterioration. Decimal currency values must not
break sentence segmentation during this check.

Evaluator-only scenario metadata remains downstream of the run and never enters
agent prompts, business definitions, or tools.

## Operational command

```bash
uv run python scripts/evaluate_canonical_workspace.py \
  .runs/canonical-mvp/canonical-q2-mvp
```

This command is safe for deterministic/offline use and must not load `.env` or
require an API key.

## Consequences

- A run can be debugged and reevaluated without paying for another model run.
- Final report, Critic, and evaluator must consume the same canonical structured
  metric set.
- Acceptance failures should list exact deterministic reasons before changing
  prompts or rerunning agents.
- Fixed workspace names are mutable when `--force` is used; archival or unique
  run IDs are necessary if historical snapshots must remain reproducible.

## Verification

See `src/evaluation/canonical.py`, `scripts/evaluate_canonical_workspace.py`,
and `tests/test_canonical_acceptance.py`.
