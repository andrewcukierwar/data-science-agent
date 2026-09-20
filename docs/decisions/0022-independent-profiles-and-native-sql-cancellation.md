# 0022: Independent source profiles and native SQL cancellation

- Status: Accepted
- Date: 2026-09-20
- Scope: P0.3 only

Decision 0021 contains process-exit symptoms but does not interrupt SQL.
The reliability audit also found independent date-range questions implemented
as catastrophic joins of fact tables.

Extend `inspect_relations` with schema-driven temporal bounds and explicitly
selected scalar source-lag comparisons. Aggregate each relation independently.
Retain the existing budget, permissions, evidence inspection, and numerical
binding boundaries.

Use DuckDB's in-process connection interruption with a daemon watchdog, repeated
signalling across statement boundaries, and synchronous connection cleanup
before terminal persistence. SQL-backed async adapters forward enclosing
cancellation and drain their workers. Direct native-interruption and normal-
process-exit tests establish that this stops computation on the supported
local workload; process isolation was not necessary. This is cooperative
cancellation, with no guarantee against uninterruptible native/kernel bugs.

Freeze a separate 30-second SQL bound, configurable from 0.05 to 120 seconds,
in future manifests and bump the tool configuration contract to 1.1. Preserve
0021's exit safeguard and all frozen v8 evidence. No timeout or budget increase,
SQL optimizer, CROSS JOIN prohibition, analytical operator, or benchmark rerun.

The API, lifecycle guarantees, caveats, identity boundary and verification are
specified in the [P0.3 contract](../sql-profiling-cancellation-contract.md).
