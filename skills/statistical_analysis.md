# Statistical Analysis

Use this procedure for evidence-backed inferential analysis:

1. Define the estimand, unit of analysis, target population, null hypothesis,
   alternative hypothesis, and practical decision threshold before testing.
2. Select a test that matches the outcome, study design, pairing, independence,
   distribution, variance structure, and sample size. Prefer robust or
   sensitivity analyses when assumptions are doubtful.
3. Check assumptions explicitly: missingness, outliers, independence,
   normality where relevant, equal variance where relevant, and adequate sample
   size. Report limitations rather than hiding failed assumptions.
4. Report the estimated difference or ratio, confidence interval, effect size,
   and p-value. Interpret the interval and effect magnitude, not just whether
   p is below a threshold.
5. Separate statistical significance from practical significance. Compare the
   effect and its uncertainty with the business-relevant threshold.
6. Treat many tested metrics, segments, and periods as a multiple-testing risk.
   Pre-specify primary tests where possible and label exploratory results.
7. Treat observational period and channel comparisons as associations. Do not
   claim causality without randomization or a defensible causal design.
8. Use Python for reproducible calculations, save scripts and useful outputs in
   approved workspace directories, and cite executed evidence in findings.

For comparable period changes, use a stable metric identifier and set
value_unit to relative_change_fraction; report the relative change as a decimal
fraction (0.10 means +10%, -0.25 means -25%). Keep absolute values in their
documented business units instead of mixing percentages and decimals.
