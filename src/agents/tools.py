"""OpenAI Agents SDK function tools backed by the deterministic Phase 0 layer."""

from __future__ import annotations

import asyncio
import json
import threading
from contextvars import copy_context
from functools import partial
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents import (
    Agent,
    FunctionTool,
    RunContextWrapper,
    ToolOutputText,
    function_tool,
)
from agents.evidence import (
    event_reference_index,
    evidence_events,
    executed_references,
    finding_reference_aliases,
    resolve_citations,
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
from schemas.analytical import (
    AnalyticalBindingPointer,
    AnalyticalRequest,
    AnalyticalResultSummary,
    AnalyticalToolOutput,
)
from schemas.run_state import ArtifactKind, ToolEventStatus
from tools.analytical import AnalyticalExecutionError, AnalyticalExecutionService
from tools.results import result_json
from tools.sql import (
    RelationInspectionError,
    RelationInspectionResult,
    RelationProfileRequest,
    SourceLagRequest,
)


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
    attempt_id: str | None = None
    result_available: bool | None = None
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

    context = wrapper.context
    context.bind_tool_agent(getattr(wrapper, "agent", None))
    return context


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


def _bounded_json(value: Any, limit: int, offset: int = 0) -> dict[str, Any]:
    """Page a persisted JSON value losslessly within the existing text limit.

    Page truncation is distinct from result truncation. Concatenating
    preview_json pages in offset order reconstructs the retained result only.
    """

    normalized = _json_safe(value)
    encoded = result_json(normalized)
    if offset < 0 or offset > len(encoded):
        raise ValueError("output_offset is outside the retained output")
    if len(encoded) <= limit and isinstance(normalized, dict) and offset == 0:
        return normalized
    end = min(offset + limit, len(encoded))
    return {
        "truncated": True,
        "preview_json": encoded[offset:end],
        "offset": offset,
        "next_offset": end if end < len(encoded) else None,
        "total_chars": len(encoded),
        "result_truncated": bool(
            isinstance(normalized, dict)
            and any(
                normalized.get(key, False)
                for key in (
                    "truncated",
                    "stdout_truncated",
                    "stderr_truncated",
                    "generated_evidence_truncated",
                )
            )
        ),
    }


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


async def _run_cancellable_sql(operation, **kwargs):
    """Drain synchronous native work before propagating SDK cancellation."""

    cancel_event = threading.Event()
    # Keep the executor Future, not an intermediate Task that loop shutdown
    # can cancel independently and detach from its still-running native thread.
    worker = asyncio.get_running_loop().run_in_executor(
        None,
        copy_context().run,
        partial(operation, cancel_event=cancel_event, **kwargs),
    )
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancel_event.set()
        # A second enclosing cancellation still must not abandon the worker.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()  # Observe profile failures; the service persisted them.
        raise


@function_tool
async def inspect_relations(
    ctx: RunContextWrapper[AgentRunContext],
    profiles: list[RelationProfileRequest] | None = None,
    include_missingness: bool = False,
    source_lags: list[SourceLagRequest] | None = None,
) -> ToolOutputText:
    """Profile sources independently: counts, schema, and temporal min/max.

    Prefer this for date ranges and source freshness; no fact joins are needed.
    Null profiles discovers all relations and every DATE/TIMESTAMP column by
    type. Explicit profiles selects relations and temporal columns (null columns
    discovers all; [] omits temporal bounds). Missingness counts nulls for every
    visible column if requested, and always for selected temporal columns.
    source_lags compares explicitly named maxima with matching types: reference
    maximum minus source maximum, in seconds. No automatic calendar, completeness
    judgment, or choice between multiple dates. Strings are never guessed as dates.
    Cite tool_event_id; inspect_evidence retrieves the retained profile without
    recomputation. One call costs one SQL execution and shares its SQL deadline.
    """

    tool_name = "inspect_relations"
    try:
        context = _context(ctx)
        context.require_permission(tool_name)
        result: RelationInspectionResult = await _run_cancellable_sql(
            context.sql_service.inspect_relations,
            profiles=profiles,
            include_missingness=include_missingness,
            source_lags=source_lags,
        )
        return _sdk_response(ToolResponse.ok(tool_name, result.model_dump(mode="json")))
    except RelationInspectionError as error:
        return _sdk_response(
            ToolResponse.failed(
                tool_name,
                error.code,
                str(error),
                data={
                    "tool_event_id": error.tool_event_id,
                    "attempt_id": error.attempt_id,
                    "timed_out": error.timed_out,
                    "cancelled": error.cancelled,
                },
            )
        )
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
    view: Literal["result", "source"] = "result",
    output_offset: int = 0,
) -> ToolOutputText:
    """Inspect one cited tool event, registered artifact, or safe evidence path.

    Event IDs and unambiguous query/script IDs, paths, registered artifact IDs,
    generated-file references, and finding aliases resolve to executed output.
    Reused aliases require an explicit event ID. Failed events return failure
    diagnostics, never successful evidence. Legacy SQL events may lack rows.
    Use view="source" with a file path or artifact ID to inspect current file
    contents separately (source code is not execution output). Unexecuted files
    remain inspectable as files. State, logs, inputs and unsafe paths are blocked.
    Large outputs return preview_json pages: pass next_offset as output_offset
    with the returned canonical tool_event_id and concatenate pages to recover
    retained JSON. A truncated calculation remains incomplete after paging.
    """

    tool_name = "inspect_evidence"
    try:
        context = _context(ctx)
        context.require_permission(tool_name)
        reference = reference.strip()
        if not reference:
            raise ValueError("reference must be a non-empty string")

        if view not in {"result", "source"}:
            raise ValueError("view must be result or source")
        if output_offset < 0:
            raise ValueError("output_offset must be non-negative")
        index = event_reference_index(context.ledger)
        executed_refs = executed_references(context.ledger)
        reference_supported = reference in executed_refs
        candidates = index.get(reference, ()) if view == "result" else ()
        if view == "result" and not candidates:
            aliases = finding_reference_aliases(context.ledger)
            if reference in aliases:
                resolution = resolve_citations(
                    [reference],
                    executed_refs=executed_refs,
                    aliases=aliases,
                )
                if not resolution.is_supported:
                    raise ValueError("finding evidence is unresolved or ambiguous")
                reference_supported = True
                candidates = evidence_events(context.ledger, list(resolution.resolved))
                if not candidates:
                    raise ValueError("finding has no unambiguous execution result")
        if len(candidates) > 1:
            raise ValueError(
                "ambiguous evidence alias; inspect an explicit tool_event_id"
            )
        event = candidates[0] if candidates else None
        if event is not None:
            data = EvidenceInspection(
                reference=reference,
                reference_type="tool_event",
                tool_event_id=event.id,
                attempt_id=event.attempt_id,
                result_available=(
                    event.status is ToolEventStatus.SUCCEEDED
                    and event.output is not None
                    and (event.tool_name != "run_sql" or "rows" in event.output)
                ),
                provenance_verified=reference_supported,
                tool_name=event.tool_name,
                status=event.status.value,
                arguments=_bounded_json(
                    event.arguments,
                    context.run_config.max_text_chars,
                ),
                output=(
                    _bounded_json(
                        event.output, context.run_config.max_text_chars, output_offset
                    )
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
async def run_sql(
    ctx: RunContextWrapper[AgentRunContext],
    sql: str,
    query_id: str | None = None,
) -> ToolOutputText:
    """Execute bounded SQL against approved workspace data.

    Approved Parquet inputs are automatically registered as read-only relation
    names derived from their file stems: ``customers``, ``orders``,
    ``sessions``, and ``marketing_spend`` for the canonical dataset. Use those
    names directly. Cite the returned tool_event_id to inspect retained output
    without rerunning SQL. row_count is the fetched count (a lower bound when
    row_count_is_lower_bound is true); retained_row_count counts persisted rows.
    A truncated result is not the complete population. SQL exceeding the configured
    sql_timeout_seconds is interrupted and fails; its event cannot support evidence.
    Use inspect_relations for independent source date bounds and source lag.
    Do not use filesystem paths or ``read_parquet``; arbitrary
    filesystem access remains blocked by the execution boundary.

    Args:
        sql: SQL statement to execute through the approved DuckDB service.
        query_id: Optional reproducible identifier for the saved query file.
    """

    tool_name = "run_sql"
    try:
        context = _context(ctx)
        context.require_permission(tool_name)
        result = await _run_cancellable_sql(
            context.sql_service.execute, sql=sql, query_id=query_id
        )
        rows = []
        used_chars = 2
        for row in result.rows[: context.run_config.max_result_rows]:
            size = len(result_json(row)) + 1
            if used_chars + size > context.run_config.max_text_chars:
                break
            rows.append(row)
            used_chars += size
        data = result.model_dump(mode="json", exclude={"success", "error"})
        data["query_path"] = result.query_path.relative_to(
            context.workspace.root
        ).as_posix()
        data["rows"] = rows
        data["model_rows_truncated"] = len(rows) < len(result.rows)
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

    Cite the returned tool_event_id to inspect the retained stdout/stderr and
    generated-file metadata. Truncation flags describe omitted stream content;
    view="source" in inspect_evidence reads current script/file contents.
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
            "result_contract_version": result.result_contract_version,
            "tool_event_id": result.tool_event_id,
            "attempt_id": result.attempt_id,
            "script_id": result.script_id,
            "script_path": result.script_path,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": result.stdout_truncated or stdout_truncated,
            "stderr_truncated": result.stderr_truncated or stderr_truncated,
            "model_stdout_truncated": stdout_truncated,
            "model_stderr_truncated": stderr_truncated,
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


@function_tool(strict_mode=False)
def run_analytical(
    ctx: RunContextWrapper[AgentRunContext],
    request: AnalyticalRequest,
) -> ToolOutputText:
    """Run one typed deterministic analytical primitive on retained evidence.

    The operation discriminator selects coverage, entity_aggregate, ratio,
    contrast, reconciliation, or binary_experiment. Role guards allow coverage
    for Data Auditor; entity_aggregate, ratio, contrast, and reconciliation for
    Analyst; binary_experiment for Statistician; and that exact union for the
    Generalist. Lead and Critic cannot execute this tool.

    Source operations accept only canonical successful, complete, untruncated
    run_sql tool_event_id values. Derived operations accept canonical
    run_analytical event IDs and retained quantity names. The response includes
    the canonical tool event ID, a distinct analytical record ID, and published
    /value pointers. The full typed record is included when it fits the configured
    model limit; otherwise explicit truncation flags, typed result summaries, and
    inspect_reference identify the retained record for inspect_evidence paging.
    Inspect the result scopes, quantities, warnings, and details before authoring a
    claim. For a bound
    metric or statistical assessment, use the analytical_record source and the
    returned pointer for every numerical field; leave numerical fields and the
    claim result_id null so application finalization copies exact values and
    assigns the separate persisted claim ID.

    Specify business semantics explicitly: source, grain, date/cohort,
    population, period/window, dimensions, numerator, denominator, aggregation,
    expected grid/cadence, reconciliation tolerances, or experiment arms,
    outcome, confidence level, practical threshold, and design assumptions as
    applicable. A post-join row count is not an entity count. Sparse event dates
    are not missing reporting dates without an explicit expectation.

    Args:
        request: One strictly validated request selected by its operation field.
    """

    tool_name = "run_analytical"
    try:
        context = _context(ctx)
        context.require_analytical_operation(request.operation)
        record = AnalyticalExecutionService(context.ledger).execute(request)
        pointers = tuple(
            AnalyticalBindingPointer(
                result_index=result_index,
                quantity=quantity_name,
                pointer=(
                    f"/results/{result_index}/quantities/"
                    f"{quantity_name.replace('~', '~0').replace('/', '~1')}/value"
                ),
                value_available=quantity.value is not None,
            )
            for result_index, result in enumerate(record.results)
            for quantity_name, quantity in result.quantities.items()
        )
        output = AnalyticalToolOutput(
            tool_event_id=record.tool_event_id,
            analytical_record_id=record.result_id,
            operation=record.operation,
            inspect_reference=record.tool_event_id,
            record=record,
            record_included=True,
            result_count=len(record.results),
            result_summaries=(),
            result_summaries_truncated=False,
            binding_pointers=pointers,
            binding_pointers_truncated=False,
        )
        if len(output.model_dump_json()) > context.run_config.max_text_chars:
            summaries: list[AnalyticalResultSummary] = []
            visible_pointers: list[AnalyticalBindingPointer] = []
            output = output.model_copy(
                update={
                    "record": None,
                    "record_included": False,
                    "binding_pointers": (),
                    "binding_pointers_truncated": bool(pointers),
                    "result_summaries_truncated": bool(record.results),
                }
            )
            for result_index, result in enumerate(record.results):
                summary = AnalyticalResultSummary(
                    result_index=result_index,
                    scope=result.scope,
                    method=result.method,
                    quantities=result.quantities,
                    warnings=result.warnings,
                )
                result_pointers = [
                    pointer
                    for pointer in pointers
                    if pointer.result_index == result_index
                ]
                candidate = output.model_copy(
                    update={
                        "result_summaries": (*summaries, summary),
                        "result_summaries_truncated": (
                            result_index + 1 < len(record.results)
                        ),
                        "binding_pointers": (*visible_pointers, *result_pointers),
                        "binding_pointers_truncated": (
                            len(visible_pointers) + len(result_pointers) < len(pointers)
                        ),
                    }
                )
                if len(candidate.model_dump_json()) > context.run_config.max_text_chars:
                    break
                summaries.append(summary)
                visible_pointers.extend(result_pointers)
                output = candidate
        return _sdk_response(ToolResponse.ok(tool_name, output.model_dump(mode="json")))
    except AnalyticalExecutionError as error:
        return _sdk_response(
            ToolResponse.failed(
                tool_name,
                error.code,
                str(error),
                data={"tool_event_id": error.tool_event_id},
            )
        )
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
    run_analytical,
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
    "run_analytical",
    "run_sql",
    "save_artifact",
    "tools_for_role",
]
