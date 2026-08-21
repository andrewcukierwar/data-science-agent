# Phase 2 benchmark results

**Run date:** 2026-08-20 / 2026-08-21
**Manifest:** `phase2-task10-20260820-v8`, frozen at code revision `b7ca12c`
**Model:** `gpt-5.6-luna` (OpenAI), identical configuration for both architectures
**Matrix:** 10 scenarios x 2 architectures x 3 repetitions = 60 declared cells
**Observed:** 60/60 cells recorded, 0 missing
**Evaluator:** catalog evaluator version `1.2`, applied offline
**Total measured cost:** `$2.9719`

Raw evidence is retained under `.runs/phase2-task10-20260820-v8/`: the immutable
manifest `benchmark.json`, the pilot report `pilot.json`, the aggregate
`report.json`, the independent offline rescore `benchmark-rescored.json`, and
every per-cell workspace.

The independent offline rescore reproduced all 60 inline evaluator results with
zero disagreements in status or score.

## Headline result

**No cell in either architecture passed the evaluator rubric.** Of 60 cells, 18
were analytically evaluated and all 18 scored `fail`; the remaining 42 did not
complete and are `not_evaluated` with no analytical score.

The only statistically supported differences between the architectures are
**cost and latency**, both favoring the single-agent baseline. No supported
difference in analytical quality was found in either direction.

## Operational reliability

Denominator is the 30 declared cells per architecture.

| Architecture | Completed | Blocked | Failed | Completion rate |
| --- | ---: | ---: | ---: | ---: |
| Multi-agent (five-agent) | 6 | 20 | 4 | 20% |
| Single-agent baseline | 12 | 9 | 9 | 40% |

Failure taxonomy for non-completed cells:

| Architecture | validation | evidence_provenance | agent | timeout | unresolved_follow_up | other |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Multi-agent | 14 | 3 | 4 | 3 | 0 | 0 |
| Single-agent | 7 | 6 | 0 | 2 | 2 | 1 |

The single-agent baseline completed twice as many cells. No significance test
was applied to these completion rates, so the difference is descriptive.

Five cells across both architectures hit the frozen 300-second invocation bound
from decision 0018. Their usage is incomplete and cost unavailable by design;
they remain in the reliability denominators.

## Completion by scenario

| Scenario | Multi-agent | Single-agent |
| --- | ---: | ---: |
| `canonical-q2-profitability` | 0/3 | 2/3 |
| `channel-mix-confounding` | 0/3 | 1/3 |
| `cogs-q2-margin-deterioration` | 0/3 | 2/3 |
| `discount-refund-q2-deterioration` | 0/3 | 2/3 |
| `meaningful-ab-treatment-effect` | 2/3 | 1/3 |
| `missing-reporting-day` | 0/3 | 2/3 |
| `no-effect-ab-experiment` | 3/3 | 1/3 |
| `partial-latest-reporting-day` | 0/3 | 0/3 |
| `retention-q2-deterioration` | 0/3 | 0/3 |
| `significant-but-immaterial-ab-effect` | 1/3 | 1/3 |

Multi-agent completions are concentrated in the three A/B experiment scenarios
(6 of its 6 completions). It completed no cell in any business root-cause or
data-quality scenario. The single-agent completions are spread across the
business scenarios instead.

**This makes the two completed-cell subsets non-comparable.** They are drawn
from different scenarios, so the per-dimension means below describe different
workloads and must not be read as a head-to-head quality comparison.

## Analytical quality, evaluated cells only

| Architecture | Evaluated cells | Mean overall score | Passing cells |
| --- | ---: | ---: | ---: |
| Multi-agent | 6 | 0.780 | 0 |
| Single-agent | 12 | 0.745 | 0 |

Mean score by evaluator dimension, over each architecture's completed cells:

| Dimension | Multi-agent (n=6) | Single-agent (n=12) |
| --- | ---: | ---: |
| numeric | 0.00 | 0.03 |
| root_cause | 0.83 | 0.45 |
| statistics | 0.25 | 0.85 |
| unsupported_claims | 0.83 | 0.33 |
| data_quality | 1.00 | 0.89 |
| provenance | 0.99 | 1.00 |
| capability | 1.00 | 1.00 |
| lifecycle | 1.00 | 1.00 |
| metric_set | 1.00 | 1.00 |
| task_completeness | 0.90 | 0.90 |

The dominant analytical failure is **numerical correctness**: near zero for both
architectures. Neither reliably reproduced the evaluator's numeric ground-truth
findings within tolerance, and that alone prevents any cell from passing.

The apparent dimension differences track the scenario composition noted above —
multi-agent's completions are A/B scenarios, where `statistics` is scored
hardest, while single-agent's are business scenarios, where `root_cause` is.
They are not evidence of an architecture effect.

## Cost and latency

| Architecture | Cells with known cost | Total cost | Mean per cell | Mean latency |
| --- | ---: | ---: | ---: | ---: |
| Multi-agent | 27/30 | `$2.6699` | `$0.0989` | 378.2 s |
| Single-agent | 27/30 | `$0.3020` | `$0.0112` | 73.8 s |

The multi-agent architecture cost about 8.8x more per cell and took about 5.1x
longer. Three cells per architecture have unavailable cost because a timeout or
failure left usage unreconciled.

## Paired architecture comparisons

The report computes per-scenario paired comparisons at alpha = 0.05. Across all
scenarios and metrics: 8 `supported_difference`, 11 `not_supported`, and 89
`insufficient_sample`.

Every supported difference is cost or latency, and every one favors the
single-agent baseline (difference is single-agent minus multi-agent):

| Scenario | Metric | Mean difference | p |
| --- | --- | ---: | ---: |
| `canonical-q2-profitability` | cost (USD) | -0.1455 | 0.0036 |
| `canonical-q2-profitability` | latency (s) | -459.02 | 0.0048 |
| `channel-mix-confounding` | cost (USD) | -0.1193 | 0.0134 |
| `channel-mix-confounding` | latency (s) | -413.50 | 0.0403 |
| `missing-reporting-day` | cost (USD) | -0.1063 | 0.0077 |
| `missing-reporting-day` | latency (s) | -328.27 | 0.0111 |
| `retention-q2-deterioration` | cost (USD) | -0.1479 | 0.0008 |
| `retention-q2-deterioration` | latency (s) | -595.87 | 0.0079 |

**No analytical-quality metric produced a supported difference in either
direction.** Most quality comparisons are `insufficient_sample` because so few
cells completed in both architectures for the same scenario.

## What this does and does not establish

Established:

- Both architectures run end to end under one frozen configuration, and every
  declared cell was executed and recorded.
- The five-agent architecture is materially more expensive and slower.
- The five-agent architecture completed fewer cells than the single-agent
  baseline in this run.
- Numerical correctness is the binding analytical constraint for both.

Not established:

- That either architecture is analytically better. No quality comparison
  reached significance, and the completed-cell subsets cover different
  scenarios.
- Any claim about task success as a rate: zero cells passed, so there is no
  positive success rate to report for either architecture.
- Generalization beyond one model (`gpt-5.6-luna`), one configuration, three
  repetitions per cell, and this ten-scenario catalog.

## Limitations

- **Sample size.** Three repetitions per scenario and architecture. Most paired
  comparisons are `insufficient_sample`.
- **Model specificity.** One model, one temperature and budget configuration.
- **Evaluator strictness.** The rubric requires numeric ground-truth findings
  within tolerance; a substantively reasonable analysis that does not emit the
  expected structured numbers scores `fail`. The zero pass rate reflects rubric
  strictness as much as analytical capability, and the dimension table is the
  more informative view.
- **Non-comparable completed subsets.** Discussed above.
- **Selection.** No evaluator rule, tolerance, or budget was changed after
  results existed, and no cell was rerun or excluded because of its outcome.
  Every declared cell appears exactly once.
- **Blocked cells are not scored.** They remain operational outcomes with no
  analytical score, so analytical means describe only the cells that completed
  and are conditioned on completion.
