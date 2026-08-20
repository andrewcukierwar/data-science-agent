# Architecture Decision Log

This directory records the non-obvious Phase 0, Phase 1, and Phase 2 decisions
that must survive beyond an individual development session. The records describe
durable contracts and their rationale; they do not contain hidden model reasoning,
credentials, or evaluator-only expected values.

| Record | Decision | Status |
| --- | --- | --- |
| [0001](0001-deterministic-workspace-and-execution-boundary.md) | Isolated workspaces, approved DuckDB relations, and Docker-only Python execution | Accepted |
| [0002](0002-manager-specialist-agent-architecture.md) | Lead-manager architecture with role-bound specialist tools | Accepted |
| [0003](0003-canonical-scenario-and-profitability-definition.md) | Coherent acquisition funnel and 90-day cohort profitability definition | Accepted |
| [0004](0004-evidence-provenance-and-metric-compilation.md) | Executed evidence provenance and a canonical structured metric set | Accepted |
| [0005](0005-bounded-orchestration-and-graceful-degradation.md) | Atomic budgets, bounded loops, and constrained reporting | Accepted |
| [0006](0006-persisted-workspace-is-the-acceptance-source.md) | Persisted run state is the source for offline canonical acceptance | Accepted |
| [0007](0007-model-configuration-usage-and-cost.md) | Central model configuration, usage, and reproducible cost estimation | Accepted |
| [0008](0008-deterministic-ci-and-opt-in-live-runs.md) | Deterministic CI is separate from paid live-agent acceptance | Accepted |
| [0009](0009-audit-provenance-across-architectures.md) | Evidence-bearing audit claims and a bounded audit evidence catalog for the Lead | Accepted |
| [0010](0010-hypothesis-evidence-rule.md) | One hypothesis-evidence rule, enforced when the state transition is requested | Accepted |

The chronological failure history, present run status, operational commands,
and remaining risks are in [Phase 1 lessons](../phase1-lessons.md).

When changing one of these contracts, add a superseding decision record rather
than silently rewriting why the original choice was made. Small factual updates
such as file names or command syntax may be made in place.
