"""DuckDB execution over approved, read-only workspace inputs."""

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, ConfigDict, Field

from orchestration.ledger import ToolEventLedger
from schemas.run_state import ToolEvent, ToolEventStatus
from tools.workspace import Workspace

_QUERY_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


class QueryExecutionResult(BaseModel):
    """Result or captured error from one DuckDB query execution."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    query_id: str = Field(min_length=1)
    query_path: Path
    success: bool
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = Field(default=0, ge=0)
    error: str | None = None


class DuckDBExecutionService:
    """Execute SQL while restricting external file access to workspace inputs."""

    def __init__(
        self,
        workspace: Workspace,
        ledger: ToolEventLedger | None = None,
    ) -> None:
        self.workspace = workspace
        self.ledger = ledger
        self._validate_workspace_layout()

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
            columns, rows = self._execute_sql(sql)
        except Exception as exc:
            result = QueryExecutionResult(
                query_id=query_id,
                query_path=query_path,
                success=False,
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
            result = QueryExecutionResult(
                query_id=query_id,
                query_path=query_path,
                success=True,
                columns=columns,
                rows=rows,
                row_count=len(rows),
            )
            event = self._build_event(
                query_id=query_id,
                status=ToolEventStatus.SUCCEEDED,
                started_at=started_at,
                query_path=query_path,
                output={"columns": columns, "row_count": len(rows)},
            )

        self._emit_event(event)
        return result

    def _execute_sql(self, sql: str) -> tuple[list[str], list[list[Any]]]:
        connection = duckdb.connect(database=":memory:")
        try:
            inputs = self.workspace.inputs.resolve()
            allowed_directories = self._sql_literal(str(inputs))
            connection.execute(f"SET allowed_directories = [{allowed_directories}]")
            connection.execute("SET enable_external_access = false")
            cursor = connection.execute(sql)
            columns = [description[0] for description in cursor.description or ()]
            rows = [list(row) for row in cursor.fetchall()]
            return columns, rows
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
            },
            output=output,
            error=error,
            artifact_refs=[self._artifact_ref(query_path)],
        )

    def _emit_event(self, event: ToolEvent) -> None:
        if self.ledger is not None:
            self.ledger.append_tool_event(event)

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
