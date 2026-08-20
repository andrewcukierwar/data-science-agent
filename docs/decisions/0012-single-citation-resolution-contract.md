# 0012: One lossless citation-resolution contract

- Status: Accepted
- Date: 2026-08-20
- Phase: Phase 2
- Extends: [0004](0004-evidence-provenance-and-metric-compilation.md),
  [0010](0010-hypothesis-evidence-rule.md)
- Covers: remediation R24

## Context

Provenance was judged by four separate implementations that had drifted apart.
`agents/lead.py` kept a private copy of the resolver that shadowed the shared
one in `agents/evidence.py`. The Critic checked source lineage but never checked
that a citation resolved at all. The runtime asked whether *any* cited reference
resolved; offline scoring asked whether *all* of them did — so a workspace could
pass at runtime and fail the benchmark evaluator that scores it. The runtime
exempted qualitative findings entirely; offline scoring did not. Offline scoring
never applied the source-lineage rule the runtime enforced.

Worse, canonicalization was lossy. `canonicalize_evidence_refs` returned only
what resolved, so a claim citing `[real_query, completed_data_audit]` was
rewritten to `[real_query]` and then passed an "any reference resolves" test.
One real query laundered an invented citation sitting beside it, and the
invented one disappeared from the persisted record.

## Decision

`agents/evidence.py` owns the single contract. Every boundary — runtime
validation, Critic validation, offline evaluation, and offline rescoring —
imports the same functions, and a regression asserts the imported objects are
identical rather than merely equivalent.

Resolution is lossless. `resolve_citations` returns a `CitationResolution`
carrying what was cited, what resolved, and what did not.
`canonical_references` replaces resolved citations with the exact executed
references they stand for and preserves unresolved ones verbatim, so a
fabricated or failed reference stays visible on the claim instead of vanishing.

A material claim is supported only when **every** citation resolves. The
`any(valid_reference)` test is gone from every boundary.

`material_claims` is the one definition of which claims are held to the rule:
findings, recommendations, resolved hypotheses, metric comparisons, and
statistical assessments. An open hypothesis is deliberately absent — it is still
being tested and may cite nothing, per decision 0010 — and its citations are
left untouched rather than canonicalized, because rewriting a reference it has
not yet earned would change its meaning.

Two rules the runtime already applied now also apply offline, because otherwise
the two boundaries demonstrably disagree on the same persisted workspace:
qualitative findings must resolve like quantitative ones, and quantitative
claims must satisfy `has_source_lineage`.

## Consequences

- The runtime gate is stricter than before: a qualitative finding or a claim
  citing one bad reference among several now fails where it previously passed.
  Aligning downward was not an option — that would weaken provenance validation,
  which is the defect being fixed. Decision 0011's bounded correction exists to
  make the stricter gate recoverable.
- Deterministic fixtures had to become production-shaped: a fixture whose SQL
  reads no approved input relation now fails the offline lineage check, which is
  the check working rather than a fixture inconvenience.
- Adding a claim type in future means adding it to `material_claims` once, and
  every boundary picks it up.

## Verification

See `src/agents/evidence.py`, `src/agents/lead.py`, `src/agents/critic.py`,
`src/evaluation/primitives.py`, and `tests/test_citation_resolution.py`.
