# 0004: Evidence provenance and canonical metric compilation

- Status: Accepted
- Date: 2026-08-17
- Phase: Phase 1

## Context

Quantitative prose is not enough for validation. Independent specialists also
naturally use different labels, IDs, units, scopes, and duplicate calculations.
Asking models to reproduce a single exact evaluator identifier proved brittle
and risked leaking scenario-specific expectations into prompts.

## Decision

Material quantitative findings must cite exact references returned by approved
execution or artifact tools. Models may not invent a path and assume it is
executed evidence. Query/script/chart/report artifacts retain relative paths,
checksums, sizes, and tool-event lineage.

A SQL query made primarily of hard-coded `VALUES` containing previously computed
numbers is not sufficient sole provenance for a material conclusion. Derived
summary SQL is acceptable only when its source-derived inputs are registered
evidence with lineage.

Use generic `MetricComparison` records for material quantitative comparisons:

- metric key;
- dimensions;
- baseline and comparison periods;
- comparison type;
- value and unit;
- evidence references;
- optional `MetricDefinitionContext` for population, date basis, observation
  window, numerator, denominator, and definition reference.

Application-boundary normalization:

- removes redundant dimension prefixes such as `meta_cac` when
  `channel=Meta` is already present;
- maps generic aliases such as `spend` to `marketing_spend`, `conversion` to
  `conversion_rate`, and `new_customers` to `acquired_customers`;
- normalizes compatible periods and relative-change units without changing
  numeric meaning;
- preserves incompatible estimands as distinct via metric definition context.

A deterministic final metric compilation step is the shared source for Lead
finalization, Critic review, report rendering, ledger persistence, and external
evaluation. It:

1. normalizes identity;
2. merges evidence references for semantically identical comparisons;
3. treats numerically consistent duplicates as corroboration;
4. exposes materially conflicting duplicate values;
5. lets corrected/remediated comparisons supersede stale values with the same
   identity;
6. retains selected specialist comparisons instead of asking the Lead to
   reconstruct values from prose.

Metric definition must remain fixed during remediation unless the Critic
specifically identifies the definition as wrong. A valid calculation with a
different population/window is labeled as a different comparison, not a
correction of the original.

## Analytical procedure supported by this decision

For material acquisition economics, the expected generic decomposition is:

`marketing spend -> sessions -> conversion -> acquired customers -> CAC -> downstream LTV/value`

For a profit-change question, the analysis also closes net revenue, COGS,
contribution before marketing, marketing spend, reporting contribution profit,
the largest relevant segment, and material non-drivers.

Before joining different fact grains, each source is aggregated to a common
reporting grain. Period/channel spend is never joined to customer/order rows and
then summed. Q1/Q2 filters use explicit boundaries and derived cohort counts are
reconciled to the customer/acquisition table.

## Consequences

- Findings and metric comparisons are related but not interchangeable.
- Critic validation checks prose, structured identity, scope, units, values,
  evidence, denominator choices, joins, and unsupported causal claims.
- Compatible repeated measurements do not automatically fail acceptance;
  material conflicts do.
- Scenario-specific ground-truth IDs and expected values stay in evaluator-only
  metadata.

## Verification

See `src/schemas/metrics.py`, `src/agents/evidence.py`,
`src/orchestration/ledger.py`, `src/agents/lead.py`, `src/agents/critic.py`,
`src/evaluation/canonical.py`, `tests/test_specialist_contracts.py`,
`tests/test_metric_definition_hardening.py`, and
`tests/test_canonical_metric_compilation.py`.
