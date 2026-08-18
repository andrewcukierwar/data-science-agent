# 0003: Canonical scenario and profitability definition

- Status: Accepted
- Date: 2026-08-17
- Phase: Phase 1

## Context

The canonical question is:

> Why did profitability decline in Q2, and what should the company do about it?

The agent sees only the injected scenario dataset and ordinary business
definitions. It does not see the clean baseline or evaluator ground truth.
Therefore the intended mechanism must be observable in Q1-to-Q2 relationships
inside the visible data and must be economically coherent.

Two earlier designs were invalid:

1. Relabeling a subset of Meta customers as Organic left their customers and
   orders intact, producing attribution movement rather than lost acquisition.
2. Treating sessions as post-acquisition customer behavior meant session
   conversion could not explain customer acquisition; customer removal and
   session conversion were being manipulated independently.

## Decision

Preserve the deterministic clean generator and inject scenarios in a separate,
typed layer under `scenarios/`.

Sessions represent an acquisition funnel:

- each acquired customer has exactly one converted acquisition session;
- the converted session occurs on `acquisition_date` and has matching channel,
  device, and `customer_id`;
- non-converting acquisition sessions are anonymous and intentionally have a
  null `customer_id`;
- non-converting traffic is generated separately;
- converted session counts exactly reconcile to acquired customer counts by
  period and channel.

The canonical injection reduces deterministic Q2 Meta conversion. The affected
would-be conversions do not create customers, and their downstream orders are
absent. It does not independently apply a second customer-removal mechanism.
Meta traffic remains comparable, spend rises, CAC deteriorates, retained cohort
LTV stays approximately stable, and non-Meta random variation must not overwhelm
the intended segment story.

The unambiguous profitability metric is:

> 90-day acquisition-cohort reporting contribution profit = the sum of
> `net_revenue - cogs` for orders from each reporting-period acquisition date
> through acquisition date + 90 days, minus marketing spend for the matching
> acquisition period and channel.

Q1 and Q2 use the same 90-day observation window. Calendar-order-date profit is
a different estimand and cannot silently replace this metric.

The exact small acceptance configuration is deterministic: seed 42, 1,000
customers, 4,000 orders, 8,000 sessions, 4 products, and a 365-day period.
Evaluator-only expected values and tolerances remain downstream and are never
written into prompts or model-visible documentation.

## Economic realism decisions

- Marketing spend scales with configured company size rather than remaining at
  the default 50,000-customer magnitude.
- Rounded `net_revenue` exactly equals gross revenue minus discount and refund.
- Order exposure is reasonably balanced for small fixtures, while timing,
  product, quantity, price, discount, refund, and COGS vary by order/customer.
- Cohort means may be proportionally normalized by the scenario injector when
  needed, but individual customer LTV and contribution distributions retain
  meaningful variance.

## Consequences

- Scenario acceptance tests must derive truth from visible scenario relations,
  not from a hidden clean-baseline comparison.
- Expected anonymous-session nulls are documented so the Auditor does not
  invent a data-quality issue.
- No broad COGS/margin shock or data defect is injected as an alternative cause.
- The acquisition funnel and order economics are now foundational invariants;
  future scenarios must preserve or explicitly supersede them.

## Verification

See `scenarios/generator/generator.py`, `scenarios/injection.py`,
`scenarios/definitions/canonical_profitability.py`, `tests/test_generator.py`,
and `tests/test_scenarios.py`.
