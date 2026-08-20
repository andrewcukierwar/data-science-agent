# 0020: The pilot cost gate measures cost, not analytical success

- Status: Accepted
- Date: 2026-08-20
- Phase: Phase 2 / Task 10

## Context

`run_pilot` selects the first declared cell of every stratum and required it to
reach `LifecycleStatus.COMPLETED` before the paid matrix could run. For the
multi-agent stratum that cell is `canonical-q2-profitability` repetition 1.

Three clean-revision pilots blocked on that cell. Two were orchestration
defects closed by decision 0019. The third, at revision `ef05ae9`, blocked after
three genuine Critic reviews with substantive analytical issues, having consumed
its declared capacity: 12 of 12 specialist invocations, 40 of 40 SQL executions,
18 of 20 Python executions, 140 requests, 607.2 seconds, and a known
`$0.16976644`.

Diagnostic probes at the same revision measured the surrounding behavior:

| Cell | Outcome | Cost | Elapsed |
| --- | --- | ---: | ---: |
| multi-agent, `canonical-q2-profitability` | blocked | `$0.16976644` | 607.2 s |
| multi-agent, `missing-reporting-day` | blocked | `$0.12078416` | 462.1 s |
| multi-agent, `no-effect-ab-experiment` | completed, scored 0.79 | `$0.01529932` | 75.8 s |
| single-agent, `canonical-q2-profitability` | completed, scored 0.83 | `$0.01214402` | 61.9 s |

Multi-agent completion is therefore scenario-dependent, not broken. A blocked
cell is a real benchmark outcome: `validation_revision` is a named block reason,
R18 preserves it, and R16 keeps it inside the reliability denominators.

Requiring completion made the cost gate demand the analytical success the
benchmark exists to measure. Because the gate always selects the same declared
cell, one scenario that reliably blocks made the entire declared matrix
unrunnable.

## Decision

- The pilot gate requires a **cost observation**, not a completed analysis.
- Accept a cell whose lifecycle status is `completed` or `blocked` and whose
  provider usage is reconciled (`usage.complete`).
- Continue to refuse `failed` and `cancelled` cells. Neither is a bounded
  measurement of a working cell, and an interrupted cell measured nothing.
- Apply the same rule at all three boundaries: pilot execution, pilot report
  construction, and the full-run gate that re-verifies the persisted report.
- Do not select a replacement cell when the declared one blocks. Substituting a
  cell that happens to complete would bias the estimate toward the cheapest
  work and make the selection arbitrary.

A blocked cell that exhausted its declared budgets is the conservative
observation: it costs at least as much as a completed cell of the same
architecture.

## Consequences

The declared matrix can run and record what actually happens, including blocked
multi-agent cells, rather than being refused before it starts. No evaluator
rule, tolerance, score, or budget changed, and no result is selected because it
favors either architecture. Non-completed cells remain `not_evaluated` with no
analytical score, exactly as before.

The published cost estimate now mixes completed and blocked cells. The pilot
report already retains every per-stratum observation and a low/high range, so
the composition stays inspectable.

## Verification

`tests/test_pilot_set_calibration.py` pins the new contract: a stratum whose
declared cell blocks with reconciled usage publishes a pilot and passes the
full-run gate, while a failed cell is still refused and no replacement cell is
selected. `tests/test_benchmark_interruption.py` keeps refusing an interrupted
cell. The deterministic suite passes 695 tests including the three Docker-backed
integrations, and Ruff passes across 168 files.
