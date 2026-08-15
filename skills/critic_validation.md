# Critic Validation

Use this procedure to independently validate candidate findings and
recommendations:

1. Read the business definitions and inspect the candidate's cited workspace
   evidence before judging a claim.
2. Use registered input relation names in new SQL rather than filesystem paths
   or `read_parquet` calls. Reproduce material numbers from saved SQL or Python. Check units, date
   windows, rounding, sample sizes, and contradictions across findings and
   artifacts.
3. Verify numerator and denominator definitions, especially CAC, conversion,
   LTV, cohort metrics, rates, and contribution profit.
4. Check joins at their actual grain for duplicate keys, row multiplication,
   unresolved foreign keys, and mismatched populations.
5. Look for ignored data-quality warnings and recommendations that exceed the
   evidence or rely on unavailable data.
6. Separate observational association from causation. Flag causal language
   that is not supported by an appropriate design.
7. Return `PASS` only when no material issue remains. Otherwise return
   `REVISE` with severity, exact evidence references, and concrete remediation.
   Do not invent defects when evidence is missing; state the limitation.
