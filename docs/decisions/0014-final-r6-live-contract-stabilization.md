# 0014: Final R6 live contract stabilization

- Status: Accepted
- Date: 2026-08-20
- Phase: Phase 2
- Extends: [0008](0008-deterministic-ci-and-opt-in-live-runs.md),
  [0009](0009-audit-provenance-across-architectures.md)
- Covers: final remediation R6 closure

## Context

The final provider-backed R6 preflight exposed three integration defects that
deterministic schema compilation did not reveal.

First, `AuditResult` and `TableAudit` used model-level `before` validators for
legacy string coercion. Pydantic then revalidated unrelated ISO-8601 values as
strict Python objects, so a provider response containing a valid JSON datetime
failed locally. Second, `AnalysisRunner` set `visualization_requested=true` for
every objective, causing the Critic to require a chart for a text-only revenue
summary. Third, the provider-visible audit schema allowed empty
`evidence_refs`; the R20 runtime boundary correctly refused such an audit, but
the provider had not been constrained to emit at least one candidate reference.

## Decision

- Legacy audit warning and limitation coercion is field-local. It must not
  change JSON parsing semantics for dates or datetimes elsewhere in the model.
- The provider-visible schemas for `AuditObservation`, `DataQualityIssue`, and
  `TableAudit` declare `minItems: 1` for `evidence_refs`. Persisted legacy audit
  contract 1.0 remains loadable with empty references so offline diagnostics can
  refuse unsupported historical claims explicitly.
- A non-empty reference is only a candidate. The existing R20 resolver remains
  authoritative and still rejects failed, fabricated, ambiguous, unrelated, or
  tampered evidence. Audit provenance failures remain terminal as decided in
  0011; this change does not add audit retries or application-authored citations.
- Multi-agent chart completeness is enforced only when the objective explicitly
  requests a chart, graph, plot, or visualization. The Critic must not infer a
  requested deliverable from architecture choice alone.
- Because the provider-visible contract changed,
  `output_schema_fingerprint()` advances to
  `126195be58dea108393095556d38de2c90c316ebc1a6cda0664d21d866ac6bfc`.
  Historical pilots cannot authorize a new Task 10 matrix.

## Consequences

- Strict output still rejects malformed JSON and unsupported evidence, while
  valid JSON representations no longer fail because of Python-object strictness.
- Visualization requirements are estimand/task driven and architecture neutral.
- New audits cannot be schema-valid with an empty material-claim citation list;
  legacy workspaces remain readable without invented provenance.
- Task 10 must freeze a new manifest and run a new R19 pilot at a clean code
  revision.

## Verification

The final revision passed 677 deterministic tests including three Docker-backed
integrations, Ruff, all 60 declared dry-run cells, retained-artifact validation,
and four provider-backed preflight tests in 81.07 seconds. The multi-agent
canary completed with validation `pass`, 84,300 tokens, one reconciled attempt,
and estimated cost `$0.00767636`; the single-agent canary completed with
validation `pass`, 36,032 tokens, one reconciled attempt, and estimated cost
`$0.00371480`.

See `src/schemas/audit.py`, `src/orchestration/runner.py`,
`tests/test_strict_agent_outputs.py`, and
`tests/test_strict_output_canary_live.py`.
