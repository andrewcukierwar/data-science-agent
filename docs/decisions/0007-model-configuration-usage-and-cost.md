# 0007: Model configuration, usage, and cost

- Status: Accepted
- Date: 2026-08-17
- Phase: Phase 1

## Context

Live runs need a model selected from environment configuration, but pricing is
not a secret and should be reviewable and reproducible. Cached input must be
priced separately from uncached input.

## Decision

- Use `OPENAI_DEFAULT_MODEL` as the application default model variable.
- Load `.env` only at application/script entry points such as
  `scripts/run_canonical_mvp.py`, not throughout library code.
- Keep `OPENAI_API_KEY` out of source control, logs, and documentation values.
- Keep model pricing in the centralized source registry
  `src/orchestration/pricing.py`, not `.env`.
- Allow explicit CLI pricing overrides for unknown or temporary model pricing.
- Record model/provider identity, request count, input/output/total/cached/
  reasoning tokens, elapsed time, and a typed cost breakdown in the ledger.
- Finalize usage, elapsed time, and cost before report rendering so reports do
  not show stale metadata.

The cost formula is:

```text
uncached_input_tokens = input_tokens - cached_tokens

cost =
    uncached_input_tokens / 1_000_000 * input_rate
  + cached_tokens / 1_000_000 * cached_input_rate
  + output_tokens / 1_000_000 * output_rate
```

The repository currently contains a configured historical/application rate for
`gpt-5.6-luna`: $0.20 uncached input, $0.02 cached input, and $1.20 output per
million tokens. This is application configuration, not a claim that provider
pricing can never change; update the registry deliberately when rates change.

## Alternatives considered

- Pricing in `.env` was rejected because it is not a secret and would make run
  estimates less reviewable.
- A single blended input rate was rejected because caching materially affects
  cost.
- Failing runs for unknown pricing was rejected; usage remains valid and cost
  may be null until pricing is configured.

## Consequences

- Live-run cost estimates are reproducible from persisted token counts and
  pricing rates.
- A model missing from the registry can run, but a complete cost estimate needs
  all three override rates.
- Model and pricing identity must be included with every persisted breakdown.

## Verification

See `src/schemas/run_state.py`, `src/orchestration/pricing.py`,
`src/orchestration/runner.py`, `scripts/run_canonical_mvp.py`, and
`tests/test_pricing.py`.
