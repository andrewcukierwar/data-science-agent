"""OpenAI Agents SDK function tools backed by the deterministic Phase 0 layer."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agents import (
    Agent,
    FunctionTool,
    RunContextWrapper,
    ToolOutputText,
    function_tool,
)
from agents.runtime import (
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    PermissionDeniedError,
    ToolResponse,
    allowed_tools_for_role,
)
from orchestration.budgets import BudgetResource
from schemas.run_state import ArtifactKind
from tools.sql import RelationInspectionResult


class WorkspaceFileInfo(BaseModel):
    """Small file listing entry returned by ``inspect_workspace``."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)


class WorkspaceInspection(BaseModel):
    """Bounded overview of agent-visible workspace files."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    directories: list[str] = Field(min_length=1)
    files: list[WorkspaceFileInfo] = Field(default_factory=list)
    file_limit: int = Field(ge=1)
    truncated: bool = False


class DocumentContents(BaseModel):
    """Bounded text returned by ``read_document``."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    content: str
    character_limit: int = Field(ge=1)
    truncated: bool = False


class EvidenceInspection(BaseModel):
    """Bounded metadata and content for one cited evidence reference."""

    model_config = ConfigDict(extra="forbid")

    reference: str = Field(min_length=1)
    reference_type: str = Field(min_length=1)
    tool_event_id: str | None = None
    artifact_id: str | None = None
    artifact_kind: ArtifactKind | None = None
    path: str | None = None
    tool_name: str | None = None
    status: str | None = None
    arguments: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    error: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    size_bytes: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    provenance_verified: bool | None = None
    content: str | None = None
    content_limit: int | None = Field(default=None, ge=1)
    truncated: bool = False


def _context(wrapper: RunContextWrapper[AgentRunContext]) -> AgentRunContext:
    """Extract the application context from an SDK wrapper."""

    return wrapper.context


def _sdk_response(response: ToolResponse) -> ToolOutputText:
    """Encode the typed response as compact JSON for the SDK model channel."""

    return ToolOutputText(text=response.model_dump_json())


def _error_response(tool_name: str, error: Exception) -> ToolOutputText:
    """Convert expected runtime errors into concise model-visible results."""

    code = getattr(
        error,
        "code",
        "not_found" if isinstance(error, FileNotFoundError) else "tool_error",
    )
    return _sdk_response(ToolResponse.failed(tool_name, code, str(error)))


def _json_safe(value: Any) -> Any:
    """Normalize database-native values before returning them to the model."""

    return json.loads(json.dumps(value, default=str))


def _truncate_text(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def _bounded_json(value: Any, limit: int) -> dict[str, Any]:
    """Keep arbitrary persisted event payloads within the model context budget."""

    normalized = _json_safe(value)
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= limit and isinstance(normalized, dict):
        return normalized
    preview, _ = _truncate_text(encoded, limit)
    return {"truncated": True, "preview_json": preview}


def _file_provenance(path: Path) -> tuple[str, int]:
    digest = sha256()
    size_bytes = 0
    with path.open("rb") as evidence_file:
        while chunk := evidence_file.read(1024 * 1024):
            digest.update(chunk)
            size_bytes += len(chunk)
    return digest.hexdigest(), size_bytes


def _evidence_file(
    context: AgentRunContext,
    reference: str,
) -> tuple[Path, str]:
    """Resolve only an approved working/ or outputs/ evidence path."""

    normalized = reference.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if not parts or parts[0] not in {"working", "outputs"}:
        raise FileNotFoundError(f"evidence reference not found: {reference}")
    return _safe_relative_file(
        context,
        normalized,
        directory_name=parts[0],
        allow_directory_prefix=False,
    )


def _evidence_content(path: Path, limit: int) -> tuple[str | None, bool]:
    """Read bounded text evidence while leaving binary artifacts summarized."""

    if path.suffix.lower() not in {
        ".csv",
        ".json",
        ".md",
        ".py",
        ".sql",
        ".tsv",
        ".txt",
        ".yaml",
        ".yml",
    }:
        return None, False
    with path.open("r", encoding="utf-8") as evidence_file:
        content = evidence_file.read(limit + 1)
    return _truncate_text(content, limit)


def _safe_relative_file(
    context: AgentRunContext,
    raw_path: str,
    *,
    directory_name: str,
    allow_directory_prefix: bool = True,
) -> tuple[Path, str]:
    """Resolve a non-symlinked file inside one approved workspace directory."""

    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("path must be a non-empty string")

    normalized = raw_path.replace("\\", "/")
    pure_path = PurePosixPath(normalized)
    if (
        not pure_path.parts
        or pure_path.is_absolute()
        or ".." in pure_path.parts
        or normalized.startswith("./")
        or (len(normalized) > 1 and normalized[1] == ":")
    ):
        raise ValueError("path must be relative and cannot contain traversal")

    parts = pure_path.parts
    if parts[0] != directory_name:
        if not allow_directory_prefix:
            raise ValueError(f"path must be inside {directory_name}/")
        parts = (directory_name, *parts)
    if len(parts) < 2:
        raise ValueError("path must identify a file")

    relative_path = PurePosixPath(*parts).as_posix()
    workspace_root = context.workspace.root.resolve()
    candidate = workspace_root.joinpath(*parts)
    approved_root = getattr(context.workspace, directory_name).resolve()
    current = workspace_root
    for part in parts:
        current /= part
        if current.is_symlink():
            raise PermissionError("path cannot contain symlink components")

    if not candidate.exists():
        raise FileNotFoundError(f"file does not exist: {relative_path}")
    if not candidate.is_file():
        raise IsADirectoryError(f"path is not a file: {relative_path}")
    try:
        candidate.resolve(strict=True).relative_to(approved_root)
    except ValueError as exc:
        raise PermissionError(
            f"path resolves outside approved {directory_name}/ directory"
        ) from exc
    return candidate, relative_path


def _workspace_files(
    context: AgentRunContext,
) -> tuple[list[WorkspaceFileInfo], bool]:
    """Collect a bounded, safe file listing without exposing state or logs."""

    files: list[WorkspaceFileInfo] = []
    truncated = False
    directories = ("inputs", "docs", "working", "outputs")
    for directory_name in directories:
        directory = getattr(context.workspace, directory_name)
        for candidate in sorted(directory.rglob("*"), key=lambda path: path.as_posix()):
            if not candidate.is_file():
                continue
            if len(files) >= context.run_config.max_workspace_files:
                truncated = True
                return files, truncated
            try:
                relative_path = candidate.relative_to(context.workspace.root).as_posix()
                _safe_relative_file(
                    context,
                    relative_path,
                    directory_name=directory_name,
                    allow_directory_prefix=False,
                )
            except (FileNotFoundError, PermissionError, ValueError):
                continue
            files.append(
                WorkspaceFileInfo(
                    path=relative_path,
                    size_bytes=candidate.stat().st_size,
                )
            )
    return files, truncated


@function_tool
def inspect_workspace(
    ctx: RunContextWrapper[AgentRunContext],
) -> ToolOutputText:
    """List approved workspace files with bounded paths and sizes.

    The listing includes inputs, docs, working, and outputs. Internal state and
    log directories are intentionally omitted.
    """

    tool_name = "inspect_workspace"
    try:
        context = _context(ctx)
        context.require_permission(tool_name)
        files, truncated = _workspace_files(context)
        data = WorkspaceInspection(
            run_id=context.run_config.run_id,
            directories=["inputs", "docs", "working", "outputs"],
            files=files,
            file_limit=context.run_config.max_workspace_files,
            truncated=truncated,
        )
        return _sdk_response(ToolResponse.ok(tool_name, data.model_dump(mode="json")))
    except (PermissionDeniedError, ValueError, OSError) as error:
        return _error_response(tool_name, error)


@function_tool
def inspect_relations(
    ctx: RunContextWrapper[AgentRunContext],
) -> ToolOutputText:
    """Inspect approved input relation names, columns, types, and row counts.

    This reads metadata from the same registered DuckDB views used by
    ``run_sql``. It does not accept filesystem paths or execute model-authored
    schema SQL. The returned source paths are relative to ``inputs/``.
    """

    tool_name = "inspect_relations"
    try:
        context = _context(ctx)
        context.require_permission(tool_name)
        result: RelationInspectionResult = context.sql_service.inspect_relations()
        return _sdk_response(ToolResponse.ok(tool_name, result.model_dump(mode="json")))
    except (PermissionDeniedError, ValueError, OSError) as error:
        return _error_response(tool_name, error)
    except Exception as error:
        return _error_response(tool_name, error)


@function_tool
def read_document(
    ctx: RunContextWrapper[AgentRunContext],
    path: str,
) -> ToolOutputText:
    """Read one text document from the read-only docs directory.

    Args:
        path: Relative document path, with or without the ``docs/`` prefix.
    """

    tool_name = "read_document"
    try:
        context = _context(ctx)
        context.require_permission(tool_name)
        document_path, relative_path = _safe_relative_file(
            context,
            path,
            directory_name="docs",
        )
        content, truncated = _truncate_text(
            document_path.read_text(encoding="utf-8"),
            context.run_config.max_document_chars,
        )
        data = DocumentContents(
            path=relative_path,
            content=content,
            character_limit=context.run_config.max_document_chars,
            truncated=truncated,
        )
        return _sdk_response(ToolResponse.ok(tool_name, data.model_dump(mode="json")))
    except (PermissionDeniedError, ValueError, OSError, UnicodeError) as error:
        return _error_response(tool_name, error)


@function_tool
def inspect_evidence(
    ctx: RunContextWrapper[AgentRunContext],
    reference: str,
) -> ToolOutputText:
    """Inspect one cited tool event, registered artifact, or safe evidence path.

    The reference must be a persisted tool-event ID, artifact ID/path, or a
    workspace-relative file under ``working/`` or ``outputs/``. State, logs,
    inputs, absolute paths, and traversal paths are never exposed.
    """

    tool_name = "inspect_evidence"
    try:
        context = _context(ctx)
        context.require_permission(tool_name)
        reference = reference.strip()
        if not reference:
            raise ValueError("reference must be a non-empty string")

        event = next(
            (item for item in context.ledger.tool_events if item.id == reference),
            None,
        )
        if event is not None:
            data = EvidenceInspection(
                reference=reference,
                reference_type="tool_event",
                tool_event_id=event.id,
                tool_name=event.tool_name,
                status=event.status.value,
                arguments=_bounded_json(
                    event.arguments,
                    context.run_config.max_text_chars,
                ),
                output=(
                    _bounded_json(event.output, context.run_config.max_text_chars)
                    if event.output is not None
                    else None
                ),
                error=event.error,
                artifact_refs=event.artifact_refs,
            )
            return _sdk_response(
                ToolResponse.ok(tool_name, data.model_dump(mode="json"))
            )

        artifact = next(
            (
                item
                for item in context.ledger.artifacts
                if item.id == reference or item.path == reference
            ),
            None,
        )
        if artifact is not None:
            evidence_path, relative_path = _evidence_file(context, artifact.path)
            try:
                verified = context.artifact_manager.verify_artifact(artifact.id)
            except (KeyError, OSError, ValueError):
                verified = False
            content, truncated = _evidence_content(
                evidence_path,
                context.run_config.max_text_chars,
            )
            data = EvidenceInspection(
                reference=reference,
                reference_type="artifact",
                artifact_id=artifact.id,
                artifact_kind=artifact.kind,
                path=relative_path,
                size_bytes=artifact.size_bytes,
                sha256=artifact.sha256,
                provenance_verified=verified,
                content=content,
                content_limit=(
                    context.run_config.max_text_chars if content is not None else None
                ),
                truncated=truncated,
            )
            return _sdk_response(
                ToolResponse.ok(tool_name, data.model_dump(mode="json"))
            )

        evidence_path, relative_path = _evidence_file(context, reference)
        checksum, size_bytes = _file_provenance(evidence_path)
        content, truncated = _evidence_content(
            evidence_path,
            context.run_config.max_text_chars,
        )
        data = EvidenceInspection(
            reference=reference,
            reference_type="workspace_file",
            path=relative_path,
            size_bytes=size_bytes,
            sha256=checksum,
            content=content,
            content_limit=(
                context.run_config.max_text_chars if content is not None else None
            ),
            truncated=truncated,
        )
        return _sdk_response(ToolResponse.ok(tool_name, data.model_dump(mode="json")))
    except (PermissionDeniedError, ValueError, OSError, UnicodeError) as error:
        return _error_response(tool_name, error)


@function_tool
def run_sql(
    ctx: RunContextWrapper[AgentRunContext],
    sql: str,
    query_id: str | None = None,
) -> ToolOutputText:
    """Execute bounded SQL against approved workspace data.

    Approved Parquet inputs are automatically registered as read-only relation
    names derived from their file stems: ``customers``, ``orders``,
    ``sessions``, and ``marketing_spend`` for the canonical dataset. Use those
    names directly. Do not use filesystem paths or ``read_parquet``; arbitrary
    filesystem access remains blocked by the execution boundary.

    Args:
        sql: SQL statement to execute through the approved DuckDB service.
        query_id: Optional reproducible identifier for the saved query file.
    """

    tool_name = "run_sql"
    try:
        context = _context(ctx)
        context.require_permission(tool_name)
        result = context.sql_service.execute(sql, query_id=query_id)
        rows = result.rows[: context.run_config.max_result_rows]
        data = {
            "query_id": result.query_id,
            "query_path": result.query_path.relative_to(
                context.workspace.root
            ).as_posix(),
            "columns": result.columns,
            "rows": _json_safe(rows),
            "row_count": result.row_count,
            "max_rows": result.max_rows,
            "truncated": result.truncated,
            "model_rows_truncated": len(result.rows)
            > context.run_config.max_result_rows,
            "truncation_message": result.truncation_message,
        }
        if not result.success:
            return _sdk_response(
                ToolResponse.failed(
                    tool_name,
                    "execution_failed",
                    result.error or "SQL execution failed",
                    data=data,
                )
            )
        return _sdk_response(ToolResponse.ok(tool_name, data))
    except (PermissionDeniedError, ValueError, OSError) as error:
        return _error_response(tool_name, error)
    except Exception as error:  # DuckDB errors are captured as a tool failure.
        return _error_response(tool_name, error)


@function_tool
def run_python(
    ctx: RunContextWrapper[AgentRunContext],
    source: str,
    script_id: str | None = None,
    timeout_seconds: float | None = None,
) -> ToolOutputText:
    """Execute analysis Python through the Docker-backed service.

    A successful run returns exact ``generated_evidence`` references for new or
    modified files under ``working/`` and ``outputs/``. Copy those references
    verbatim into later finding evidence; do not construct a path manually.

    Python runs in a separate isolated container and does not inherit the
    DuckDB connection or registered SQL views from ``run_sql``. To read raw
    approved inputs, use pandas or PyArrow with paths under
    ``/workspace/inputs``; do not open a fresh DuckDB connection expecting
    ``customers``, ``orders``, ``sessions``, or ``marketing_spend`` views.

    Args:
        source: Python source code to persist under working/scripts/.
        script_id: Optional reproducible identifier for the saved script.
        timeout_seconds: Optional wall-clock timeout for this execution.
    """

    tool_name = "run_python"
    try:
        context = _context(ctx)
        context.require_permission(tool_name)
        result = context.python_service.run_python(
            source,
            script_id=script_id,
            timeout_seconds=timeout_seconds,
        )
        stdout, stdout_truncated = _truncate_text(
            result.stdout,
            context.run_config.max_text_chars,
        )
        stderr, stderr_truncated = _truncate_text(
            result.stderr,
            context.run_config.max_text_chars,
        )
        data = {
            "script_id": result.script_id,
            "script_path": result.script_path,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "exit_code": result.exit_code,
            "duration_seconds": result.duration_seconds,
            "timed_out": result.timed_out,
            "generated_evidence": [
                item.model_dump(mode="json") for item in result.generated_evidence
            ],
            "generated_evidence_refs": [
                item.evidence_ref for item in result.generated_evidence
            ],
            "generated_evidence_truncated": result.generated_evidence_truncated,
        }
        if not result.success:
            return _sdk_response(
                ToolResponse.failed(
                    tool_name,
                    "execution_failed",
                    result.error or "Python execution failed",
                    data=data,
                )
            )
        return _sdk_response(ToolResponse.ok(tool_name, data))
    except (PermissionDeniedError, ValueError, OSError) as error:
        return _error_response(tool_name, error)
    except Exception as error:
        return _error_response(tool_name, error)


@function_tool
def save_artifact(
    ctx: RunContextWrapper[AgentRunContext],
    path: str,
    artifact_id: str,
    kind: ArtifactKind = ArtifactKind.OTHER,
    media_type: str | None = None,
    description: str | None = None,
) -> ToolOutputText:
    """Register an existing working/ or outputs/ file with provenance.

    Args:
        path: Relative path inside working/ or outputs/.
        artifact_id: Stable identifier for the persisted artifact.
        kind: Artifact kind: query, script, chart, report, or other.
        media_type: Optional MIME type.
        description: Optional concise description of the artifact.
    """

    tool_name = "save_artifact"
    try:
        context = _context(ctx)
        context.require_permission(tool_name)
        kind = ArtifactKind(kind)
        if kind is ArtifactKind.CHART:
            context.consume_budget(BudgetResource.CHARTS_CREATED)
        artifact = context.artifact_manager.register(
            path,
            artifact_id=artifact_id,
            kind=kind,
            media_type=media_type,
            description=description,
        )
        return _sdk_response(
            ToolResponse.ok(tool_name, artifact.model_dump(mode="json"))
        )
    except (PermissionDeniedError, ValueError, OSError) as error:
        return _error_response(tool_name, error)
    except Exception as error:
        return _error_response(tool_name, error)


_ALL_TOOLS: tuple[FunctionTool, ...] = (
    inspect_workspace,
    read_document,
    inspect_relations,
    run_sql,
    run_python,
    save_artifact,
    inspect_evidence,
)


def tools_for_role(role: AgentRole | str) -> list[FunctionTool]:
    """Return only the SDK tools visible to a role.

    Tool handlers repeat the permission check at invocation time so a tool
    object cannot be misused if it is manually attached to another Agent.
    """

    role = AgentRole(role)
    allowed = {tool.name for tool in _ALL_TOOLS if tool.name in _role_tools(role)}
    return [tool for tool in _ALL_TOOLS if tool.name in allowed]


def _role_tools(role: AgentRole) -> frozenset[str]:
    """Return the permission map without exposing mutable registry state."""

    return allowed_tools_for_role(role)


def build_agent(
    name: str,
    role: AgentRole | str,
    *,
    model: str | None = None,
    instructions: str | None = None,
) -> Agent[AgentRunContext]:
    """Build a plain SDK Agent with the shared role-scoped tool surface."""

    role = AgentRole(role)
    return Agent(
        name=name,
        model=model,
        instructions=instructions
        or (
            "Use the available deterministic workspace tools and report concise "
            "evidence."
        ),
        tools=tools_for_role(role),
    )


def build_agent_from_config(
    name: str,
    config: AgentRunConfig,
    *,
    instructions: str | None = None,
) -> Agent[AgentRunContext]:
    """Build a role-scoped Agent directly from run configuration."""

    return build_agent(
        name,
        config.agent_role,
        model=config.model,
        instructions=instructions,
    )


__all__ = [
    "DocumentContents",
    "EvidenceInspection",
    "WorkspaceFileInfo",
    "WorkspaceInspection",
    "build_agent",
    "build_agent_from_config",
    "inspect_relations",
    "inspect_workspace",
    "inspect_evidence",
    "read_document",
    "run_python",
    "run_sql",
    "save_artifact",
    "tools_for_role",
]
