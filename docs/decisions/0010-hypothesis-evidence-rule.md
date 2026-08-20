# 0010: One hypothesis-evidence rule, checked at the transition

- Status: Accepted
- Date: 2026-08-20
- Phase: Phase 2
- Extends: [0004](0004-evidence-provenance-and-metric-compilation.md),
  [0009](0009-audit-provenance-across-architectures.md)
- Covers: remediation R22

## Context

Five places wrote or read hypothesis evidence and none of them stated the same
rule. The Lead instructions said only to resolve a hypothesis "when the returned
evidence supports that disposition". The `Hypothesis` contract said nothing.
`record_hypothesis` accepted any transition and persisted it. Final Lead
validation rejected unsupported resolutions — but only after the entire run had
finished. Offline evaluation applied its own inline `status != "open"`
comparison.

The practical consequence was that an invalid resolution entered the ledger and
its append-only history, the model never learned it was invalid and so could not
correct it, the run failed at the very end, and a resumed run could inherit the
poisoned state. Decision 0009 gave the audit the provenance the Lead needs; this
decision governs what the Lead is allowed to do with it.

## Decision

One rule, stated once and enforced at every boundary:

> An open hypothesis may carry no evidence. Every supported, rejected, or
> inconclusive hypothesis must cite canonical executed evidence.

`schemas.hypotheses.hypothesis_requires_evidence` is the single predicate the
state tool, final Lead validation, and offline evaluation all call, so they
cannot drift on which transitions need provenance.

The contract states the rule where the model can see it. A Pydantic validator
would not: model validators never appear in a JSON schema, so enforcing it there
would move the error without communicating the rule, and would convert an
easily-correctable provenance problem into a strict-schema failure — the wrong
taxonomy, and outside the bounded correction path R23 adds. The rule therefore
lives in the `status` and `evidence_refs` field descriptions, which the strict
output schema carries to the provider, and in the Lead and Generalist
instructions, including for qualitative and data-quality hypotheses resolved
from the audit.

Validation happens when the transition is requested. `record_hypothesis`
resolves the citations before the ledger is touched, so a refused resolution
leaves the current hypothesis, the append-only history, the
`rejected_hypotheses` index, and the persisted file unchanged, and a resumed run
reads the pre-transition state. The refusal is a typed `invalid_hypothesis_transition`
tool error carrying the hypothesis ID, the requested status, which references
failed to resolve, which resolved, a bounded list of references that are
actually available, and an explicit remedy: keep the hypothesis open, or cite an
available reference — never invent one.

An accepted resolution is persisted with its canonical references, so the
intermediate state, the final Lead result, and offline scoring read the same
thing. An open hypothesis is left untouched, references included: quietly
dropping a reference the model still intends to use would be the same silent
rewrite this contract exists to prevent.

Offline evaluation checks the append-only history as well as the current
hypothesis list. Revising a claim must not erase that it was once asserted
without support, and the history check catches unsupported transitions in
workspaces the current runtime never produced.

The `Hypothesis` field descriptions are part of the strict output schema, so
`output_schema_fingerprint()` changes and a new benchmark manifest is required
before paid execution.

## Consequences

- A model that resolves a hypothesis without support learns immediately, in the
  tool response, while it can still act on it — instead of failing the run after
  the final response.
- The ledger stays a state store: it persists what it is given, and the evidence
  resolver stays in the agent layer where it can reach executed references. Low
  level `update_hypothesis` calls are unvalidated by design, which is why the
  offline evaluator checks persisted state independently.
- Adding a hypothesis status in future means deciding, once, whether
  `hypothesis_requires_evidence` returns true for it.

## Verification

See `src/schemas/hypotheses.py`, `src/agents/hypothesis_state.py`,
`src/evaluation/primitives.py`, and `tests/test_hypothesis_transitions.py`.
