# 0019: Resolve deterministic completeness before spending a Critic loop

- Status: Accepted
- Date: 2026-08-20
- Phase: Phase 2 / Task 10 readiness

## Context

The first clean-revision R19 pilot after decision 0018 blocked its multi-agent
cell with `validation_revision` after 92 requests, 940,854 tokens, 479.58
seconds, and `$0.12697552`. No resource budget was exhausted: the cell used 10
of 12 specialist invocations, 32 of 40 SQL executions, 10 of 20 Python
executions, and 1 of 4 charts.

Only the first of its three Critic loops was a model review. `run_critic`
consumes a critic-loop budget unit and then short-circuits on the deterministic
completeness gates in `candidate_completeness_validation`, so cycles two and
three returned `V-COMPLETENESS-FOLLOW-UP` and `V-COMPLETENESS-MARGIN` without
ever reaching the Critic model.

The runner resolved those gates once, before the first Critic review, with a
bounded completion pass whose prompt explicitly requires
`follow_up_analysis=false`. Every later remediation ran with
`allow_follow_up=False` under a prompt that never stated that contract. A
remediated candidate that re-raised `follow_up_analysis` therefore could not be
completed and could not be reviewed: each remaining Critic loop was spent
rediscovering a free check.

The replacement pilot at the fixed revision then blocked the same way on a
different gate. `V-COMPLETENESS-MARGIN` requires a profitability candidate to
complete the revenue, COGS, contribution-before-marketing, and margin
comparison. The candidate carried all four as typed `MetricComparison` objects
— `net_revenue`, `cogs`, `contribution_before_marketing`, `contribution_margin`,
and `cogs_to_revenue_ratio` — and stated "Broad margin deterioration was
therefore not a material driver". The gate additionally required one of a fixed
list of literal phrases such as `not material` or `margin was stable`, none of
which is a substring of that sentence, so it rejected a completed comparison
twice and consumed the remaining review budget. The issue message it published
("does not complete the ... comparison") was factually wrong about the
candidate.

## Decision

- Evaluate the deterministic completeness gates in the runner before each
  Critic invocation, not only before the first one.
- When a gate fires, resolve it with the existing bounded completion pass and
  re-derive the candidate, so paid Critic capacity is spent on model review.
- Bound completion passes to `MAX_LEAD_COMPLETION_PASSES = 2` for the whole
  run. Once the bound is reached, the candidate goes to the Critic and the gate
  becomes a terminal, honest block exactly as before.
- State the bounded-continuation contract in the remediation prompt: the
  replacement `LeadResult` must set `follow_up_analysis=false`, completing
  materially useful follow-up inside that call, or record the unanswerable
  question as an open question or caveat.
- Treat the four typed metric comparisons as satisfying
  `V-COMPLETENESS-MARGIN`. The structured comparison is the completeness
  evidence the gate names; whether the candidate then dispositions margin
  correctly is an analytical judgment that belongs to the Critic model, which
  the gate would otherwise pre-empt. The prose-only path keeps its existing
  component and disposition requirements unchanged.

## Consequences

Remediation still receives no additional follow-up continuation cycle, so the
property decision commit `2c94d17` protected is preserved. Analytical work
stays bounded by the unchanged shared resource limits — 40 SQL executions, 20
Python executions, 12 specialist invocations, 4 charts, 3 critic loops — so
neither architecture gains analytical headroom; the change only stops a free
deterministic check from consuming a paid review. No evaluator rule, tolerance,
or score changed.

The single-agent architecture is unaffected: it performs one bounded run with
an internal self-critique and no critic-loop budget, and its instructions
already require `follow_up_analysis=false`.

## Verification

`tests/test_critic.py` pins the margin gate against the exact pilot candidate
shape: four typed comparisons plus a paraphrased non-driver conclusion passes
the deterministic gate, while a profitability answer that ignores COGS entirely
still raises `V-COMPLETENESS-MARGIN`.

`tests/test_runner.py` covers both directions: a remediated candidate that
re-raises `follow_up_analysis` gets one completion pass and then a real Critic
review of a completed candidate, and a Lead that never clears the gate consumes
at most `MAX_LEAD_COMPLETION_PASSES` passes before the Critic runs and the run
terminates. The full deterministic suite passes 694 tests including the three
Docker-backed integrations, and Ruff passes across 167 files.
