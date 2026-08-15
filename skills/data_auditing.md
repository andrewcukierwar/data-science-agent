# Data Auditing

Use this procedure for a reproducible preflight audit:

1. Inspect all available files and read the relevant business definitions
   before interpreting column names, dates, revenue, or customer status.
2. For each table, record file format, columns, types, row count, date columns,
   minimum/maximum dates, and expected temporal grain.
3. Measure missingness for every important field and check duplicate rates for
   candidate identifiers. A repeated foreign key is normal; duplicate a row
   only when the candidate key repeats.
4. Identify likely primary keys and plausible relationships. Check that foreign
   keys resolve to the referenced table before treating a join as reliable.
5. Look for suspicious temporal gaps, invalid dates, impossible numeric values,
   broken references, and unusually extreme counts. Compare with neighboring
   periods or documented business rules where possible.
6. Use bounded SQL for counts, schemas, nulls, duplicates, date coverage, and
   relationship checks. Use reproducible Python for checks that need profiling,
   distribution summaries, or anomaly calculations.
7. Record only observed, actionable issues with evidence references to executed
   query/script paths or tool events. If a check cannot establish a problem,
   record a limitation rather than inventing a data-quality finding.
8. Return a concise internal `AuditResult` with status, per-table facts, issues,
   and limitations. Do not turn the audit into a user-facing business report.
