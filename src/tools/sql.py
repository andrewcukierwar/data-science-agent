"""DuckDB execution over approved, read-only workspace inputs."""

import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import duckdb
from pydantic import BaseModel, ConfigDict, Field

from orchestration.ledger import ToolEventLedger
from schemas.run_state import ToolEvent, ToolEventStatus
from tools.results import (
    EXECUTION_RESULT_CONTRACT_VERSION,
    MAX_SQL_RESULT_BYTES,
    json_result_value,
    result_json,
)
from tools.sql_deadline import (
    DEFAULT_SQL_TIMEOUT_SECONDS,
    SQLCancelledError,
    SQLTimeoutError,
    bounded_connection,
    check_sql_cancelled,
    validate_sql_timeout,
)
from tools.workspace import Workspace

_QUERY_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_RELATION_NAME_PATTERN = re.compile(r"[a-z_][a-z0-9_]*\Z")
_DEFAULT_MAX_ROWS = 10_000
_MAX_ALLOWED_ROWS = 100_000
_MAX_RELATIONS_IN_INSPECTION = 100
_MAX_COLUMNS_PER_RELATION = 256
_TRUNCATION_GUIDANCE = (
    "The result was truncated at max_rows. Aggregate or filter the query "
    "before retrieving more rows."
)


class InputRelationError(ValueError):
    """Raised when an input Parquet file cannot safely become a relation."""


class RelationInspectionError(ValueError):
    """A persisted profile failure with inspectable diagnostic identity."""

    def __init__(self, error: Exception, event_id: str | None, attempt_id: str | None):
        super().__init__(f"{type(error).__name__}: {error}")
        self.tool_event_id = event_id
        self.attempt_id = attempt_id
        self.timed_out = isinstance(error, SQLTimeoutError)
        self.cancelled = isinstance(error, SQLCancelledError)
        self.code = getattr(error, "code", "execution_failed")


class QueryExecutionResult(BaseModel):
    """Result or captured error from one DuckDB query execution."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    result_contract_version: str = EXECUTION_RESULT_CONTRACT_VERSION
    tool_event_id: str | None = None
    attempt_id: str | None = None
    query_id: str = Field(min_length=1)
    query_path: Path
    success: bool
    timed_out: bool = False
    cancelled: bool = False
    sql_timeout_seconds: float = DEFAULT_SQL_TIMEOUT_SECONDS
    columns: list[str] = Field(default_factory=list)
    column_types: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = Field(default=0, ge=0)
    retained_row_count: int = Field(default=0, ge=0)
    row_count_is_lower_bound: bool = False
    max_result_bytes: int = MAX_SQL_RESULT_BYTES
    max_rows: int = Field(default=_DEFAULT_MAX_ROWS, ge=1)
    truncated: bool = False
    truncation_message: str | None = None
    error: str | None = None


class RelationColumnMetadata(BaseModel):
    """One column exposed by an approved input relation."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    data_type: str = Field(min_length=1)
    nullable: bool | None = None
    null_count: int | None = Field(default=None, ge=0)
    temporal_profiled: bool = False
    minimum: str | None = None
    maximum: str | None = None


class RelationProfileRequest(BaseModel):
    """Choose a relation and explicit temporal columns; null discovers all."""

    model_config = ConfigDict(extra="forbid")
    relation_name: str = Field(min_length=1)
    temporal_columns: list[str] | None = Field(default=None, max_length=256)


class SourceLagRequest(BaseModel):
    """Compare maxima only for explicitly named, compatible temporal columns."""

    model_config = ConfigDict(extra="forbid")
    relation_name: str
    column_name: str
    reference_relation: str
    reference_column: str


class SourceLagResult(SourceLagRequest):
    """Reference maximum minus source maximum; null if either has no values."""

    lag_seconds: float | None = None


class RelationMetadata(BaseModel):
    """Bounded schema metadata for one approved input relation."""

    model_config = ConfigDict(extra="forbid")

    relation_name: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    columns: list[RelationColumnMetadata] = Field(default_factory=list)
    columns_truncated: bool = False
    row_count: int | None = Field(default=None, ge=0)
    temporal_status: Literal[
        "profiled", "no_temporal_columns", "not_requested", "schema_truncated"
    ] = "not_requested"


class RelationInspectionResult(BaseModel):
    """Typed metadata returned for approved registered input relations."""

    model_config = ConfigDict(extra="forbid")

    result_contract_version: str = EXECUTION_RESULT_CONTRACT_VERSION
    profile_contract_version: str = "1.0"
    attempt_id: str | None = None
    sql_timeout_seconds: float = DEFAULT_SQL_TIMEOUT_SECONDS
    source_lags: list[SourceLagResult] = Field(default_factory=list)
    coverage_caveat: str = (
        "Observed bounds do not establish reporting completeness or expected cadence. "
        "Sparse events can legitimately omit dates; "
        "source lag does not establish causality."
    )
    relations: list[RelationMetadata] = Field(default_factory=list)
    total_relations: int = Field(ge=0)
    relation_limit: int = Field(ge=1)
    truncated: bool = False
    row_counts_included: bool = False
    # Exact provenance for the metadata below. An agent that states a row
    # count, column, or date-coverage fact from this response can cite this
    # reference instead of having no way to prove where the fact came from.
    tool_event_id: str | None = None


class SQLExecutionLedger(ToolEventLedger, Protocol):
    """Ledger boundary required by the SQL execution service."""

    def increment_budget(self, **usage: int) -> object:
        """Increment observable run usage."""


class DuckDBExecutionService:
    """Execute SQL while restricting external file access to workspace inputs."""

    def __init__(
        self,
        workspace: Workspace,
        ledger: SQLExecutionLedger | None = None,
        *,
        max_rows: int = _DEFAULT_MAX_ROWS,
        sql_timeout_seconds: float = DEFAULT_SQL_TIMEOUT_SECONDS,
    ) -> None:
        self.workspace = workspace
        self.ledger = ledger
        self.max_rows = self._validate_max_rows(max_rows)
        self.sql_timeout_seconds = validate_sql_timeout(sql_timeout_seconds)
        self._validate_workspace_layout()
        self._input_relations = self._discover_input_relations()

    @property
    def input_relations(self) -> dict[str, Path]:
        """Return a copy of the approved relation-to-file mapping."""

        return dict(self._input_relations)

    def inspect_relations(
        self,
        *,
        include_row_counts: bool = True,
        include_missingness: bool = False,
        profiles: list[RelationProfileRequest] | None = None,
        source_lags: list[SourceLagRequest] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> RelationInspectionResult:
        """Profile independent sources independently, under one SQL deadline.

        Discover every DATE/TIMESTAMP column by type, or select named columns.
        No string-to-date guessing, implicit date choice, expected calendar, or
        cross-relation fact joins. One call consumes one SQL budget unit.
        """

        started_at = datetime.now(UTC)
        event_id = f"tool-inspect-relations-{uuid.uuid4().hex}"
        attempt_id = getattr(getattr(self.ledger, "state", None), "attempt_id", None)
        arguments = {
            "include_row_counts": include_row_counts,
            "include_missingness": include_missingness,
            "profiles": [p.model_dump() for p in profiles]
            if profiles is not None
            else None,
            "source_lags": [p.model_dump() for p in source_lags or []],
            "relation_limit": _MAX_RELATIONS_IN_INSPECTION,
            "sql_timeout_seconds": self.sql_timeout_seconds,
        }
        try:
            self._reserve_execution_budget()
            result = self._inspect_relations(
                include_row_counts=include_row_counts,
                include_missingness=include_missingness,
                profiles=profiles,
                source_lags=source_lags or [],
                cancel_event=cancel_event,
            )
            check_sql_cancelled(cancel_event)
            result.tool_event_id = event_id if self.ledger is not None else None
            result.attempt_id = attempt_id
            if len(result_json(result.model_dump(mode="json"))) > MAX_SQL_RESULT_BYTES:
                raise ValueError(
                    "profile exceeds the persisted result byte limit; "
                    "select fewer relations/columns"
                )
        except Exception as exc:
            self._emit_event(
                ToolEvent(
                    id=event_id,
                    attempt_id=attempt_id,
                    tool_name="inspect_relations",
                    status=ToolEventStatus.FAILED,
                    started_at=started_at,
                    completed_at=datetime.now(UTC),
                    arguments=arguments,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            raise RelationInspectionError(
                exc, event_id if self.ledger is not None else None, attempt_id
            ) from exc
        self._emit_event(
            ToolEvent(
                id=event_id,
                attempt_id=attempt_id,
                tool_name="inspect_relations",
                status=ToolEventStatus.SUCCEEDED,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                arguments=arguments,
                output=result.model_dump(mode="json"),
            )
        )
        return result

    def execute(
        self,
        sql: str,
        *,
        query_id: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> QueryExecutionResult:
        """Save and execute SQL, returning rows or a structured error."""

        if not isinstance(sql, str) or not sql.strip():
            raise ValueError("sql must be a non-empty string")

        query_id = query_id or f"Q-{uuid.uuid4().hex}"
        self._validate_query_id(query_id)
        query_path = self.workspace.working / "queries" / f"{query_id}.sql"
        if query_path.exists():
            raise FileExistsError(f"query artifact already exists: {query_path}")
        self._reserve_execution_budget()
        with query_path.open("x", encoding="utf-8") as query_file:
            query_file.write(sql)

        started_at = datetime.now(UTC)
        event_id = f"tool-sql-{uuid.uuid4().hex}"
        attempt_id = getattr(getattr(self.ledger, "state", None), "attempt_id", None)
        identity = {
            "tool_event_id": event_id if self.ledger is not None else None,
            "attempt_id": attempt_id,
            "query_id": query_id,
            "query_path": query_path,
            "max_rows": self.max_rows,
            "sql_timeout_seconds": self.sql_timeout_seconds,
        }
        try:
            columns, column_types, rows, row_limit_hit = self._execute_sql(
                sql, cancel_event=cancel_event
            )
            result = QueryExecutionResult(
                **identity,
                success=True,
                columns=columns,
                column_types=column_types,
                row_count=len(rows),
                row_count_is_lower_bound=row_limit_hit,
                truncated=row_limit_hit,
                truncation_message=_TRUNCATION_GUIDANCE if row_limit_hit else None,
            )
            # Reserve room for truncation metadata before retaining whole rows.
            # A wide cell cannot bypass the row limit to inflate persisted output.
            overhead = len(result_json(self._result_output(result))) + 512
            if overhead > MAX_SQL_RESULT_BYTES:
                raise ValueError(
                    "SQL result columns exceed the persisted result byte limit"
                )
            used = overhead
            for row in rows:
                normalized = json_result_value(row)
                size = len(result_json(normalized)) + 1
                if used + size > MAX_SQL_RESULT_BYTES:
                    result.truncated = True
                    result.truncation_message = (
                        "The result was truncated by the persisted result byte limit. "
                        "Aggregate or filter the query. Retained rows are not "
                        "the complete population."
                    )
                    break
                result.rows.append(normalized)
                used += size
            result.retained_row_count = len(result.rows)
            check_sql_cancelled(cancel_event)
        except Exception as exc:
            result = QueryExecutionResult(
                **identity,
                success=False,
                timed_out=isinstance(exc, SQLTimeoutError),
                cancelled=isinstance(exc, SQLCancelledError),
                error=f"{type(exc).__name__}: {exc}",
            )

        event = self._build_event(
            event_id=event_id,
            attempt_id=attempt_id,
            query_id=query_id,
            status=ToolEventStatus.SUCCEEDED
            if result.success
            else ToolEventStatus.FAILED,
            started_at=started_at,
            query_path=query_path,
            output=self._result_output(result) if result.success else None,
            error=result.error,
        )
        self._emit_event(event)
        return result

    def _result_output(self, result: QueryExecutionResult) -> dict[str, Any]:
        output = result.model_dump(mode="json", exclude={"success", "error"})
        output["query_path"] = self._artifact_ref(result.query_path)
        return output

    def _execute_sql(
        self,
        sql: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> tuple[list[str], list[str], list[list[Any]], bool]:
        with bounded_connection(self.sql_timeout_seconds, cancel_event) as connection:
            inputs = self.workspace.inputs.resolve()
            allowed_directories = self._sql_literal(str(inputs))
            connection.execute(f"SET allowed_directories = [{allowed_directories}]")
            self._register_input_views(connection)
            connection.execute("SET enable_external_access = false")
            cursor = connection.execute(sql)
            columns = [description[0] for description in cursor.description or ()]
            column_types = [
                str(description[1]) for description in cursor.description or ()
            ]
            rows = [list(row) for row in cursor.fetchmany(self.max_rows + 1)]
            truncated = len(rows) > self.max_rows
            return columns, column_types, rows[: self.max_rows], truncated

    def _inspect_relations(
        self,
        *,
        include_row_counts: bool,
        include_missingness: bool,
        profiles: list[RelationProfileRequest] | None,
        source_lags: list[SourceLagRequest],
        cancel_event: threading.Event | None,
    ) -> RelationInspectionResult:
        requests = (
            profiles
            if profiles is not None
            else [
                RelationProfileRequest(relation_name=name)
                for name in self._input_relations
            ]
        )
        names = [p.relation_name for p in requests]
        if len(set(names)) != len(names):
            raise ValueError("duplicate profile relation")
        if set(names) - self._input_relations.keys():
            raise ValueError("unknown profile relation")
        if profiles is not None and len(profiles) > _MAX_RELATIONS_IN_INSPECTION:
            raise ValueError("too many requested profile relations")
        if len(source_lags) > _MAX_RELATIONS_IN_INSPECTION:
            raise ValueError("too many source lag comparisons")
        with bounded_connection(self.sql_timeout_seconds, cancel_event) as connection:
            inputs = self.workspace.inputs.resolve()
            connection.execute(
                f"SET allowed_directories = [{self._sql_literal(str(inputs))}]"
            )
            self._register_input_views(connection)
            connection.execute("SET enable_external_access = false")
            relations = []
            bounds = {}
            for request in requests[:_MAX_RELATIONS_IN_INSPECTION]:
                relation = request.relation_name
                description = connection.execute(
                    f"DESCRIBE {self._quote_identifier(relation)}"
                ).fetchmany(_MAX_COLUMNS_PER_RELATION + 1)
                columns = [
                    RelationColumnMetadata(
                        name=row[0],
                        data_type=row[1],
                        nullable=row[2] == "YES",
                    )
                    for row in description[:_MAX_COLUMNS_PER_RELATION]
                ]
                temporal = {
                    c.name
                    for c in columns
                    if c.data_type == "DATE" or c.data_type.startswith("TIMESTAMP")
                }
                selected = (
                    temporal
                    if request.temporal_columns is None
                    else set(request.temporal_columns)
                )
                if not selected <= temporal:
                    raise ValueError(
                        "requested temporal column is absent, not DATE/TIMESTAMP, "
                        "or outside the column limit"
                    )
                expressions = (
                    ["COUNT(*)"]
                    if (include_row_counts or include_missingness or selected)
                    else []
                )
                for column in columns:
                    quoted = self._quote_identifier(column.name)
                    if include_missingness or column.name in selected:
                        expressions.append(f"COUNT(*) - COUNT({quoted})")
                    if column.name in selected:
                        expressions.extend([f"MIN({quoted})", f"MAX({quoted})"])
                values = iter(
                    connection.execute(
                        f"SELECT {', '.join(expressions)} "
                        f"FROM {self._quote_identifier(relation)}"
                    ).fetchone()
                    if expressions
                    else []
                )
                count = int(next(values)) if expressions else None
                for column in columns:
                    if include_missingness or column.name in selected:
                        column.null_count = int(next(values))
                    if column.name in selected:
                        minimum, maximum = next(values), next(values)
                        column.temporal_profiled = True
                        column.minimum = json_result_value(minimum)
                        column.maximum = json_result_value(maximum)
                        bounds[(relation, column.name)] = (column.data_type, maximum)
                relations.append(
                    RelationMetadata(
                        relation_name=relation,
                        source_path=self._input_relations[relation]
                        .relative_to(inputs)
                        .as_posix(),
                        columns=columns,
                        columns_truncated=len(description) > _MAX_COLUMNS_PER_RELATION,
                        row_count=count if include_row_counts else None,
                        temporal_status="schema_truncated"
                        if len(description) > _MAX_COLUMNS_PER_RELATION
                        else "profiled"
                        if selected
                        else ("not_requested" if temporal else "no_temporal_columns"),
                    )
                )
            lags = []
            for request in source_lags:
                source = bounds.get((request.relation_name, request.column_name))
                reference = bounds.get(
                    (request.reference_relation, request.reference_column)
                )
                if source is None or reference is None:
                    raise ValueError(
                        "source lag requires explicitly profiled temporal columns"
                    )
                if source[0] != reference[0]:
                    raise ValueError(
                        "source lag requires matching temporal types; "
                        "no implicit time-zone conversion"
                    )
                lag = (
                    None
                    if source[1] is None or reference[1] is None
                    else (reference[1] - source[1]).total_seconds()
                )
                lags.append(SourceLagResult(**request.model_dump(), lag_seconds=lag))
            return RelationInspectionResult(
                relations=relations,
                source_lags=lags,
                total_relations=len(self._input_relations),
                relation_limit=_MAX_RELATIONS_IN_INSPECTION,
                truncated=len(requests) > _MAX_RELATIONS_IN_INSPECTION,
                row_counts_included=include_row_counts,
                sql_timeout_seconds=self.sql_timeout_seconds,
            )

    def _build_event(
        self,
        *,
        event_id: str,
        attempt_id: str | None,
        query_id: str,
        status: ToolEventStatus,
        started_at: datetime,
        query_path: Path,
        output: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> ToolEvent:
        return ToolEvent(
            id=event_id,
            attempt_id=attempt_id,
            tool_name="run_sql",
            status=status,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            arguments={
                "query_id": query_id,
                "query_path": self._artifact_ref(query_path),
                "max_rows": self.max_rows,
                "sql_timeout_seconds": self.sql_timeout_seconds,
            },
            output=output,
            error=error,
            artifact_refs=[self._artifact_ref(query_path)],
        )

    def _emit_event(self, event: ToolEvent) -> None:
        if self.ledger is not None:
            self.ledger.append_tool_event(event)

    def _reserve_execution_budget(self) -> None:
        if self.ledger is None:
            return
        reserve = getattr(self.ledger, "reserve_budget", None)
        if callable(reserve):
            reserve("sql_executions")
        else:
            # Preserve compatibility with small Phase 0 test doubles that do
            # not expose the persistent ledger reservation API.
            self.ledger.increment_budget(sql_executions=1)

    def _validate_workspace_layout(self) -> None:
        root_path = self.workspace.root
        root = root_path.resolve()
        inputs = self.workspace.inputs
        working = self.workspace.working
        queries = working / "queries"
        if (
            not root.is_dir()
            or root_path.is_symlink()
            or inputs.is_symlink()
            or working.is_symlink()
            or queries.is_symlink()
            or inputs.resolve().parent != root
            or working.resolve().parent != root
            or queries.resolve().parent != working.resolve()
            or not inputs.is_dir()
            or not queries.is_dir()
        ):
            raise ValueError("workspace does not have a safe inputs and queries layout")

    def _discover_input_relations(self) -> dict[str, Path]:
        """Discover safe Parquet inputs and derive unique SQL relation names."""

        inputs = self.workspace.inputs.resolve()
        relations: dict[str, Path] = {}
        for path in sorted(inputs.rglob("*")):
            if path.is_symlink():
                raise InputRelationError(
                    f"workspace inputs cannot contain symlinks: {path}"
                )
            if not path.is_file() or path.suffix.lower() != ".parquet":
                continue

            resolved = path.resolve()
            try:
                resolved.relative_to(inputs)
            except ValueError as exc:
                raise InputRelationError(
                    f"Parquet input escapes the approved inputs directory: {path}"
                ) from exc

            relation = self._sanitize_relation_name(path.stem)
            previous = relations.get(relation)
            if previous is not None:
                raise InputRelationError(
                    "duplicate approved input relation "
                    f"'{relation}' from {previous} and {path}"
                )
            relations[relation] = path
        return relations

    def _register_input_views(self, connection: duckdb.DuckDBPyConnection) -> None:
        """Register validated Parquet inputs as read-only DuckDB views."""

        for relation, path in self._input_relations.items():
            connection.execute(
                f"CREATE VIEW {self._quote_identifier(relation)} AS "
                f"SELECT * FROM read_parquet({self._sql_literal(str(path))})"
            )

    @staticmethod
    def _sanitize_relation_name(stem: str) -> str:
        """Convert a file stem into a conservative SQL identifier.

        Punctuation and whitespace become underscores, names are normalized to
        lowercase, and a leading digit receives an underscore prefix. Empty
        results are rejected instead of creating an unusable relation.
        """

        relation = re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_").lower()
        if relation and relation[0].isdigit():
            relation = f"_{relation}"
        if not _RELATION_NAME_PATTERN.fullmatch(relation):
            raise InputRelationError(
                f"unsafe Parquet input stem {stem!r}: cannot derive a SQL relation"
            )
        return relation

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        """Quote an already validated SQL identifier defensively."""

        return '"' + identifier.replace('"', '""') + '"'

    def _artifact_ref(self, path: Path) -> str:
        return path.relative_to(self.workspace.root).as_posix()

    @staticmethod
    def _validate_query_id(query_id: str) -> None:
        if not isinstance(query_id, str) or not _QUERY_ID_PATTERN.fullmatch(query_id):
            raise ValueError(
                "query_id must start with a letter or digit and contain only "
                "letters, digits, underscores, or hyphens"
            )

    @staticmethod
    def _sql_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    @staticmethod
    def _validate_max_rows(max_rows: int) -> int:
        if (
            not isinstance(max_rows, int)
            or isinstance(max_rows, bool)
            or not 1 <= max_rows <= _MAX_ALLOWED_ROWS
        ):
            raise ValueError(
                f"max_rows must be an integer between 1 and {_MAX_ALLOWED_ROWS}"
            )
        return max_rows
