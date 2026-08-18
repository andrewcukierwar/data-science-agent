# 0001: Deterministic workspace and execution boundary

- Status: Accepted
- Date: 2026-08-17
- Phase: Phase 0 foundation, retained in Phase 1

## Context

Agent-generated SQL and Python need useful access to run data without gaining
general access to the host or mutating source inputs. Analysis must remain
reproducible and auditable even when model output is incorrect or adversarial.

## Decision

Each run uses a dedicated workspace with this contract:

- `inputs/` and `docs/` are copied into the run and made read-only;
- `working/` and `outputs/` are writable analysis locations;
- `state/` and `logs/` hold durable application state and observability;
- artifact paths are stored relative to the workspace root;
- artifact registration is limited to `working/` and `outputs/`, rejects
  absolute paths, traversal, and symlink escapes, and records SHA-256 and size;
- existing artifacts are not silently overwritten.

DuckDB automatically exposes safe Parquet files in `inputs/` as read-only
relations named from validated file stems. Agents query `customers`, `orders`,
`sessions`, and `marketing_spend`, not `read_parquet()` paths. Arbitrary file
read functions remain blocked. SQL result materialization is bounded and
reports truncation metadata. `inspect_relations` exposes concise relation,
column, type, source, and optional row-count metadata so agents do not guess
schemas or author `information_schema` discovery queries.

Python analysis runs only in Docker:

- scripts are persisted under `working/scripts/` before execution;
- `inputs/` and `docs/` are mounted read-only;
- `working/` and `outputs/` are mounted read-write;
- networking is disabled;
- the process is non-root and has CPU, memory, and wall-clock limits;
- `MPLCONFIGDIR` points to a writable `/tmp` location;
- stdout, stderr, exit code, duration, timeout, and errors are typed and logged.

The Python container does not inherit DuckDB's in-process registered views. A
Python script reads approved raw files below `/workspace/inputs` with pandas or
PyArrow. This separation is part of the security contract, not an incidental
implementation detail.

## Alternatives considered

- Direct host Python was simpler but exposed the host filesystem and network.
- Passing `read_parquet()` paths to models was flexible but expanded the path
  attack surface and caused repeated schema/path mistakes.
- Postgres or a remote sandbox would add deployment complexity before the local
  analytical behavior was proven.

## Consequences

- Deterministic and Docker integration tests are both required.
- Docker-dependent tests may skip with an explicit reason when Docker is
  unavailable, but must run where the daemon is available.
- The application must detect new or modified approved files after Python runs
  and attach checksum/size evidence to the corresponding tool event.
- A pre-existing file cannot become “executed evidence” merely because a model
  names its path.
- Workspace cleanup is deliberately scoped to one validated run ID.

## Verification

Relevant implementations and tests include:

- `src/tools/workspace.py`, `src/tools/artifacts.py`, `src/tools/sql.py`;
- `src/sandbox/executor.py`, `src/tools/python.py`;
- `tests/test_workspace.py`, `tests/test_artifacts.py`, `tests/test_sql.py`;
- `tests/test_sandbox.py`, `tests/test_python.py`,
  `tests/test_python_docker.py`, and `tests/test_phase0_integration.py`.
