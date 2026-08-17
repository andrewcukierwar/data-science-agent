# Business Analytics

Use this procedure for evidence-backed KPI investigation:

1. Read `docs/business_definitions.md` before computing a metric. State the
   numerator, denominator, population, time window, cohort rule, and treatment
   of refunds or cancellations.
2. Decompose the target KPI into its major components before explaining change.
   Compare periods with consistent definitions, denominators, and reporting
   windows.
3. Identify the grain of every source before joining facts of different grains.
   Aggregate each source to the common reporting grain before joining. Never
   join period/channel marketing spend directly to customer or order rows and
   then sum spend. Reconcile row counts and aggregate totals before and after
   material joins.
4. For profitability, explicitly decompose net revenue, COGS, contribution
   before marketing, marketing spend, and reporting contribution profit. State
   whether revenue, COGS/margin, or marketing economics are material drivers or
   non-drivers. Compute and compare COGS, contribution before marketing, and
   contribution margin (or the COGS/revenue ratio); if broad margin deterioration
   is not material, state that explicitly.
5. Segment the result by relevant channel, customer, product, region, device,
   and funnel stage. Use acquisition cohorts when timing affects outcomes.
6. For acquisition, calculate CAC as channel spend divided by newly acquired
   customers. Calculate LTV over an explicit post-acquisition window and keep
   the LTV cohort, channel, and denominator aligned with CAC.
7. When acquisition economics are material to the objective, decompose:
   marketing spend -> traffic/sessions -> conversion -> acquired customers
   -> CAC -> downstream LTV/value. Compare relevant periods and segments and
   reconcile each step to the appropriate customer and order cohorts. Do not
   run the full decomposition when acquisition economics are not material.
8. Analyze funnels from sessions through conversion and orders. Reconcile
   funnel counts to customer and order tables before interpreting rates.
9. For named reporting periods, use explicit date boundaries or explicit
   quarter inclusion. Never classify every period that is not Q1 as Q2.
   Reconcile derived cohort counts to the customers/acquisition table before
   using them in inference.
10. Use `inspect_relations` or the registered input relation names (for example
   `customers`, `orders`, `sessions`, and `marketing_spend`) in SQL. Do not use
   `read_parquet` paths or other filesystem paths when querying approved inputs.
   Use SQL for bounded aggregation and joins. `run_python` executes in a
   separate isolated container and does not inherit the SQL connection or its
   registered views; read raw approved files with pandas or PyArrow under
   `/workspace/inputs` when needed. Save useful outputs under approved
   `working/` or `outputs/` paths and retain their evidence references.
11. Treat a period difference as an observation, not a causal explanation.
   Report association, limitations, and what follow-up test or data would be
   needed before making a causal claim.
12. When a result reveals a material unanswered sub-question, add it to
   `follow_up_questions` instead of silently assuming an explanation.

Every material quantitative `Finding` must include `evidence_refs` pointing to
an executed query/script path, tool event, or registered artifact. Never invent
an evidence reference or report a number that cannot be reproduced.

Return material period or segment comparisons as generic `MetricComparison`
objects as well as prose Findings. Preserve their metric identity, dimensions,
periods, comparison type, unit, value, and exact evidence references.

For a material acquisition-efficiency explanation, close the observable path in
the same answer: marketing spend -> sessions/traffic -> conversion -> acquired
customers -> CAC -> downstream LTV/value. State which links are observed and
which upstream causes remain unsupported by the available data.

For comparable period changes, use a stable metric identifier and set
value_unit to relative_change_fraction; report the relative change as a decimal
fraction (0.10 means +10%, -0.25 means -25%). Keep absolute values in their
documented business units instead of mixing percentages and decimals. When a
nonzero baseline is available, return the relative_change comparison in addition
to an absolute difference when both are material to the conclusion.
