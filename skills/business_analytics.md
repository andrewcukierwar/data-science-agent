# Business Analytics

Use this procedure for evidence-backed KPI investigation:

1. Read `docs/business_definitions.md` before computing a metric. State the
   numerator, denominator, population, time window, cohort rule, and treatment
   of refunds or cancellations.
2. Decompose the target KPI into its major components before explaining change.
   Compare periods with consistent definitions, denominators, and reporting
   windows.
3. Segment the result by relevant channel, customer, product, region, device,
   and funnel stage. Use acquisition cohorts when timing affects outcomes.
4. For acquisition, calculate CAC as channel spend divided by newly acquired
   customers. Calculate LTV over an explicit post-acquisition window and keep
   the LTV cohort, channel, and denominator aligned with CAC.
5. Analyze funnels from sessions through conversion and orders. Reconcile
   funnel counts to customer and order tables before interpreting rates.
6. Use `inspect_relations` or the registered input relation names (for example
   `customers`, `orders`, `sessions`, and `marketing_spend`) in SQL. Do not use
   `read_parquet` paths or other filesystem paths when querying approved inputs.
   Use SQL for bounded aggregation and joins. `run_python` executes in a
   separate isolated container and does not inherit the SQL connection or its
   registered views; read raw approved files with pandas or PyArrow under
   `/workspace/inputs` when needed. Save useful outputs under approved
   `working/` or `outputs/` paths and retain their evidence references.
7. Treat a period difference as an observation, not a causal explanation.
   Report association, limitations, and what follow-up test or data would be
   needed before making a causal claim.
8. When a result reveals a material unanswered sub-question, add it to
   `follow_up_questions` instead of silently assuming an explanation.

Every material quantitative `Finding` must include `evidence_refs` pointing to
an executed query/script path, tool event, or registered artifact. Never invent
an evidence reference or report a number that cannot be reproduced.

For comparable period changes, use a stable metric identifier and set
value_unit to relative_change_fraction; report the relative change as a decimal
fraction (0.10 means +10%, -0.25 means -25%). Keep absolute values in their
documented business units instead of mixing percentages and decimals.
