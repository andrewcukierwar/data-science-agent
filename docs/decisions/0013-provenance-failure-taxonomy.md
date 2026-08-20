# 0013: Provenance failures are a named operational outcome

- Status: Accepted
- Date: 2026-08-20
- Phase: Phase 2
- Extends: [0009](0009-audit-provenance-across-architectures.md),
  [0012](0012-single-citation-resolution-contract.md)
- Covers: remediation R25

## Context

R18 gave every non-completion a typed reason so operational reporting never had
to infer it from prose. Semantic citation failures were the gap it left: a run
that ended because a well-formed answer cited evidence that did not resolve fell
through `classify_exception` to `RunBlockReason.OTHER` and was published as
`FailureCategory.OTHER` — indistinguishable from a crash.

That is the single most likely failure mode of this system, and the 2026-08-20
multi-agent canary is proof: it failed exactly that way and the benchmark would
have recorded it as "other". A benchmark that cannot tell "the model made an
unsupported claim" apart from "something broke" cannot report reliability.

The second gap was that nothing prevented a repeat. The canary was a live,
paid test; there was no deterministic reproduction of the handoff it exercised,
and the lifecycle fixtures that were green throughout used empty audits, which
never exercise the audit-to-Lead handoff at all.

## Decision

Every semantic citation failure inherits one base class,
`agents.evidence.EvidenceProvenanceError` — `LeadEvidenceError`,
`AuditEvidenceError`, `HypothesisEvidenceError`, `AnalystEvidenceError`, and
`StatisticianEvidenceError`. Classification is by type, so a provenance error
added at a new boundary in future inherits the taxonomy instead of silently
landing in `other`, and no keyword matching is involved.

The taxonomy gains `RunBlockReason.EVIDENCE_PROVENANCE` and
`FailureCategory.EVIDENCE_PROVENANCE`. The reason is checked before the generic
`ModelBehaviorError` branch, because a response whose citations do not resolve
is a semantic failure of a well-formed answer, not a malformed one — calling
it a schema failure would misattribute it to the output contract.

`AttemptRecord` gains a typed `block_reason`, and `finish_attempt` inherits the
run-level reason that `mark_failed`/`mark_blocked`/`mark_cancelled` already set.
Attempt history therefore carries the same taxonomy as the run state and the
benchmark record without every call site repeating it, and a completed attempt
carries no reason at all.

The 2026-08-20 failure is retained as a deterministic regression: the real
multi-agent lifecycle, an evidence-bearing audit produced through a real
`inspect_relations` call, and scripted Lead responses citing
`completed_data_audit`. It asserts the named reason, the named category, and the
attempt-history entry — with no provider call. Sibling regressions pin that the
same handoff *recovers* when the R23 correction cites real evidence, and that
the original pre-R20 shape (an audit with no provenance at all) is now refused
at the audit boundary before the Lead ever runs.

Lifecycle fixtures that previously used empty audits now use evidence-bearing
ones whose claims cite an execution the workspace actually contains. An empty
`AuditResult` satisfies the provenance contract only because it claims nothing,
so a lifecycle test built on one stays green while the handoff it is supposed to
cover is broken — which is what happened.

## Consequences

- Operational reporting can now distinguish an unsupported claim from a crash,
  a refusal, and a schema violation, at every boundary from attempt history
  through aggregation, failure reports, and canonical offline rescore.
- Adding a provenance error at a new boundary requires no taxonomy change.
- Fixtures must contain the executions their audits cite. That is more setup,
  and it is the point.
- Pre-R25 workspaces persisted no `evidence_provenance` reason, so the prose
  inference path in `_failure_category` is deliberately left unchanged; no
  retained record needs it, and extending keyword inference is the mechanism
  this decision replaces.

## Verification

See `src/agents/evidence.py`, `src/orchestration/block_reasons.py`,
`src/benchmark/runner.py`, `tests/test_provenance_failure_taxonomy.py`, and
`tests/test_failure_taxonomy.py`.
