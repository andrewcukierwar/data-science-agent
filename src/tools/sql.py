"""DuckDB execution over approved, read-only workspace inputs."""

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import duckdb
from pydantic import BaseModel, ConfigDict, Field

from orchestration.ledger import ToolEventLedger
from schemas.run_state import ToolEvent, ToolEventStatus
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


class QueryExecutionResult(BaseModel):
    """Result or captured error from one DuckDB query execution."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    query_id: str = Field(min_length=1)
    query_path: Path
    success: bool
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = Field(default=0, ge=0)
    max_rows: int = Field(default=_DEFAULT_MAX_ROWS, ge=1)
    truncated: bool = False
    truncation_message: str | None = None
    error: str | None = None


class RelationColumnMetadata(BaseModel):
    """One column exposed by an approved input relation."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    data_type: str = Field(min_length=1)


class RelationMetadata(BaseModel):
    """Bounded schema metadata for one approved input relation."""

    model_config = ConfigDict(extra="forbid")

    relation_name: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    columns: list[RelationColumnMetadata] = Field(default_factory=list)
    columns_truncated: bool = False
    row_count: int | None = Field(default=None, ge=0)


class RelationInspectionResult(BaseModel):
    """Typed metadata returned for approved registered input relations."""

    model_config = ConfigDict(extra="forbid")

    relations: list[RelationMetadata] = Field(default_factory=list)
    total_relations: int = Field(ge=0)
    relation_limit: int = Field(ge=1)
    truncated: bool = False
    row_counts_included: bool = False


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
    ) -> None:
        self.workspace = workspace
        self.ledger = ledger
        self.max_rows = self._validate_max_rows(max_rows)
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
    ) -> RelationInspectionResult:
        """Inspect the schemas of the approved registered input relations.

        Metadata is derived from the same temporary DuckDB views used by
        :meth:`execute`. The method never accepts a path or model-authored
        relation name, and only returns bounded metadata rather than rows.
        """

        started_at = datetime.now(UTC)
        event_id = f"tool-inspect-relations-{uuid.uuid4().hex}"
        try:
            result = self._inspect_relations(include_row_counts=include_row_counts)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._emit_event(
                ToolEvent(
                    id=event_id,
                    tool_name="inspect_relations",
                    status=ToolEventStatus.FAILED,
                    started_at=started_at,
                    completed_at=datetime.now(UTC),
                    arguments={
                        "include_row_counts": include_row_counts,
                        "relation_limit": _MAX_RELATIONS_IN_INSPECTION,
                    },
                    error=error,
                )
            )
            raise

        self._emit_event(
            ToolEvent(
                id=event_id,
                tool_name="inspect_relations",
                status=ToolEventStatus.SUCCEEDED,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                arguments={
                    "include_row_counts": include_row_counts,
                    "relation_limit": _MAX_RELATIONS_IN_INSPECTION,
                },
                output=result.model_dump(mode="json"),
            )
        )
        return result

    def execute(
        self,
        sql: str,
        *,
        query_id: str | None = None,
    ) -> QueryExecutionResult:
        """Save and execute SQL, returning rows or a structured error."""

        if not isinstance(sql, str) or not sql.strip():
            raise ValueError("sql must be a non-empty string")

        query_id = query_id or f"Q-{uuid.uuid4().hex}"
        self._validate_query_id(query_id)
        query_path = self.workspace.working / "queries" / f"{query_id}.sql"
        if query_path.exists():
            raise FileExistsError(f"query artifact already exists: {query_path}")
        query_path.write_text(sql, encoding="utf-8")

        started_at = datetime.now(UTC)
        try:
            columns, rows, truncated = self._execute_sql(sql)
            truncation_message = _TRUNCATION_GUIDANCE if truncated else None
        except Exception as exc:
            result = QueryExecutionResult(
                query_id=query_id,
                query_path=query_path,
                success=False,
                max_rows=self.max_rows,
                error=f"{type(exc).__name__}: {exc}",
            )
            event = self._build_event(
                query_id=query_id,
                status=ToolEventStatus.FAILED,
                started_at=started_at,
                query_path=query_path,
                error=result.error,
            )
        else:
            event = self._build_event(
                query_id=query_id,
                status=ToolEventStatus.SUCCEEDED,
                started_at=started_at,
                query_path=query_path,
                output={
                    "columns": columns,
                    "row_count": len(rows),
                    "max_rows": self.max_rows,
                    "truncated": truncated,
                    "truncation_message": truncation_message,
                },
            )
            result = QueryExecutionResult(
                query_id=query_id,
                query_path=query_path,
                success=True,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                max_rows=self.max_rows,
                truncated=truncated,
                truncation_message=truncation_message,
            )

        self._emit_event(event)
        return result

    def _execute_sql(
        self,
        sql: str,
    ) -> tuple[list[str], list[list[Any]], bool]:
        connection = duckdb.connect(database=":memory:")
        try:
            inputs = self.workspace.inputs.resolve()
            allowed_directories = self._sql_literal(str(inputs))
            connection.execute(f"SET allowed_directories = [{allowed_directories}]")
            self._register_input_views(connection)
            connection.execute("SET enable_external_access = false")
            cursor = connection.execute(sql)
            columns = [description[0] for description in cursor.description or ()]
            rows = [list(row) for row in cursor.fetchmany(self.max_rows + 1)]
            truncated = len(rows) > self.max_rows
            return columns, rows[: self.max_rows], truncated
        finally:
            connection.close()

    def _inspect_relations(
        self,
        *,
        include_row_counts: bool,
    ) -> RelationInspectionResult:
        """Read bounded schema metadata from the approved DuckDB views."""

        connection = duckdb.connect(database=":memory:")
        try:
            inputs = self.workspace.inputs.resolve()
            allowed_directories = self._sql_literal(str(inputs))
            connection.execute(f"SET allowed_directories = [{allowed_directories}]")
            self._register_input_views(connection)
            connection.execute("SET enable_external_access = false")

            relation_items = list(self._input_relations.items())
            visible_items = relation_items[:_MAX_RELATIONS_IN_INSPECTION]
            relations: list[RelationMetadata] = []
            for relation, path in visible_items:
                description = connection.execute(
                    f"DESCRIBE {self._quote_identifier(relation)}"
                ).fetchmany(_MAX_COLUMNS_PER_RELATION + 1)
                columns_truncated = len(description) > _MAX_COLUMNS_PER_RELATION
                columns = [
                    RelationColumnMetadata(name=row[0], data_type=row[1])
                    for row in description[:_MAX_COLUMNS_PER_RELATION]
                ]
                row_count = None
                if include_row_counts:
                    row_count = int(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {self._quote_identifier(relation)}"
                        ).fetchone()[0]
                    )
                relations.append(
                    RelationMetadata(
                        relation_name=relation,
                        source_path=path.relative_to(inputs).as_posix(),
                        columns=columns,
                        columns_truncated=columns_truncated,
                        row_count=row_count,
                    )
                )
            return RelationInspectionResult(
                relations=relations,
                total_relations=len(relation_items),
                relation_limit=_MAX_RELATIONS_IN_INSPECTION,
                truncated=len(relation_items) > _MAX_RELATIONS_IN_INSPECTION,
                row_counts_included=include_row_counts,
            )
        finally:
            connection.close()

    def _build_event(
        self,
        *,
        query_id: str,
        status: ToolEventStatus,
        started_at: datetime,
        query_path: Path,
        output: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> ToolEvent:
        return ToolEvent(
            id=f"tool-{query_id}",
            tool_name="run_sql",
            status=status,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            arguments={
                "query_id": query_id,
                "query_path": self._artifact_ref(query_path),
                "max_rows": self.max_rows,
            },
            output=output,
            error=error,
            artifact_refs=[self._artifact_ref(query_path)],
        )

    def _emit_event(self, event: ToolEvent) -> None:
        if self.ledger is not None:
            self.ledger.append_tool_event(event)
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
