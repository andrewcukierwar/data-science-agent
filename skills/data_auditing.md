# Data Auditing

Use this procedure for a reproducible preflight audit:

1. Inspect all available files and read the relevant business definitions
   before interpreting column names, dates, revenue, or customer status.
2. Call the read-only `inspect_relations` tool before authoring schema SQL. It
   returns the approved relation names, source files, exact columns, DuckDB
   data types, and row counts without requiring model-authored
   `information_schema` queries.
3. For each table, record file format, columns, types, row count, date columns,
   minimum/maximum dates, and expected temporal grain.
4. Measure missingness for every important field and check duplicate rates for
   candidate identifiers. A repeated foreign key is normal; duplicate a row
   only when the candidate key repeats.
5. Identify likely primary keys and plausible relationships. Check that foreign
   keys resolve to the referenced table before treating a join as reliable.
   A documented nullable relationship can be intentional; for an acquisition
   funnel, anonymous non-converting sessions may correctly have no customer.
6. Look for suspicious temporal gaps, invalid dates, impossible numeric values,
   broken references, and unusually extreme counts. Compare with neighboring
   periods or documented business rules where possible.
7. Use the registered input relation names (for example `customers`, `orders`,
   `sessions`, and `marketing_spend`) in SQL rather than filesystem paths or
   `read_parquet` calls. Use bounded SQL for counts, schemas, nulls, duplicates,
   date coverage, and relationship checks. Use reproducible Python for checks
   that need profiling, distribution summaries, or anomaly calculations.
8. `run_python` runs in a separate isolated container. It does not inherit the
   DuckDB connection or registered SQL views from `run_sql`; read raw approved
   files with pandas or PyArrow under `/workspace/inputs` when profiling needs
   Python.
9. Record only observed, actionable issues with evidence references to executed
   query/script paths or tool events. If a check cannot establish a problem,
   record a limitation rather than inventing a data-quality finding.
10. Return a concise internal `AuditResult` with status, per-table facts, issues,
   and limitations. Do not turn the audit into a user-facing business report.
