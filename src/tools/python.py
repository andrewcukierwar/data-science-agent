"""High-level Docker-backed Python analysis execution service."""

import re
import uuid
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from orchestration.ledger import ToolEventLedger
from sandbox.executor import DockerSandboxExecutor, SandboxExecutionResult
from schemas.run_state import ToolEvent, ToolEventStatus
from tools.workspace import Workspace

_SCRIPT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


class PythonExecutionResult(BaseModel):
    """Typed result returned by the Python analysis service."""

    model_config = ConfigDict(extra="forbid")

    script_id: str = Field(min_length=1)
    script_path: str = Field(min_length=1)
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_seconds: float = Field(ge=0)
    timed_out: bool = False
    error: str | None = None


class PythonExecutionLedger(ToolEventLedger, Protocol):
    """Ledger boundary required by the Python execution service."""

    def increment_budget(self, **usage: int) -> object:
        """Increment observable run usage."""


class PythonExecutionService:
    """Persist and execute analysis Python through the Docker sandbox."""

    def __init__(
        self,
        workspace: Workspace,
        ledger: PythonExecutionLedger | None = None,
        *,
        executor: DockerSandboxExecutor | None = None,
        image: str = "data-science-agent-python:latest",
        memory_limit: str = "512m",
        cpu_limit: float = 1.0,
        pids_limit: int = 128,
        timeout_seconds: float = 30.0,
        max_event_output_chars: int = 4_000,
    ) -> None:
        self.workspace = workspace
        self.ledger = ledger
        self.executor = executor or DockerSandboxExecutor(
            workspace,
            image=image,
            memory_limit=memory_limit,
            cpu_limit=cpu_limit,
            pids_limit=pids_limit,
            timeout_seconds=timeout_seconds,
        )
        if (
            not isinstance(max_event_output_chars, int)
            or isinstance(max_event_output_chars, bool)
            or max_event_output_chars < 256
        ):
            raise ValueError("max_event_output_chars must be an integer >= 256")
        self.max_event_output_chars = max_event_output_chars
        self._validate_workspace_layout()

    def run_python(
        self,
        source: str,
        *,
        script_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> PythonExecutionResult:
        """Persist source code and execute it in the isolated sandbox."""

        if not isinstance(source, str) or not source.strip():
            raise ValueError("source must be a non-empty string")

        script_id = script_id or f"P-{uuid.uuid4().hex}"
        self._validate_script_id(script_id)
        relative_path = f"working/scripts/{script_id}.py"
        script_path = self.workspace.root / relative_path
        try:
            with script_path.open("x", encoding="utf-8") as script_file:
                script_file.write(source)
        except FileExistsError as exc:
            raise FileExistsError(
                f"Python script already exists: {relative_path}"
            ) from exc

        started_at = datetime.now(UTC)
        try:
            sandbox_result = self.executor.execute(
                relative_path,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            sandbox_result = SandboxExecutionResult(
                success=False,
                duration_seconds=0,
                error=f"{type(exc).__name__}: {exc}",
            )

        result = PythonExecutionResult(
            script_id=script_id,
            script_path=relative_path,
            success=sandbox_result.success,
            stdout=sandbox_result.stdout,
            stderr=sandbox_result.stderr,
            exit_code=sandbox_result.exit_code,
            duration_seconds=sandbox_result.duration_seconds,
            timed_out=sandbox_result.timed_out,
            error=self._result_error(sandbox_result),
        )
        event_stdout, stdout_truncated = self._truncate_event_text(result.stdout)
        event_stderr, stderr_truncated = self._truncate_event_text(result.stderr)
        event = ToolEvent(
            id=f"tool-{script_id}",
            tool_name="run_python",
            status=(
                ToolEventStatus.SUCCEEDED if result.success else ToolEventStatus.FAILED
            ),
            started_at=started_at,
            completed_at=datetime.now(UTC),
            arguments={
                "script_id": script_id,
                "script_path": relative_path,
                "timeout_seconds": timeout_seconds
                if timeout_seconds is not None
                else getattr(self.executor, "timeout_seconds", None),
                "memory_limit": getattr(self.executor, "memory_limit", None),
                "cpu_limit": getattr(self.executor, "cpu_limit", None),
                "pids_limit": getattr(self.executor, "pids_limit", None),
            },
            output={
                "stdout": event_stdout,
                "stderr": event_stderr,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
                "exit_code": result.exit_code,
                "duration_seconds": result.duration_seconds,
                "timed_out": result.timed_out,
            },
            error=result.error,
            artifact_refs=[relative_path],
        )
        self._record(event)
        return result

    def execute(
        self,
        source: str,
        *,
        script_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> PythonExecutionResult:
        """Compatibility alias for :meth:`run_python`."""

        return self.run_python(
            source,
            script_id=script_id,
            timeout_seconds=timeout_seconds,
        )

    def _record(self, event: ToolEvent) -> None:
        if self.ledger is not None:
            self.ledger.append_tool_event(event)
            self.ledger.increment_budget(python_executions=1)

    def _truncate_event_text(self, value: str) -> tuple[str, bool]:
        if len(value) <= self.max_event_output_chars:
            return value, False
        return value[: self.max_event_output_chars], True

    @staticmethod
    def _result_error(sandbox_result: SandboxExecutionResult) -> str | None:
        if sandbox_result.success:
            return None
        return (
            sandbox_result.error
            or sandbox_result.stderr.strip()
            or "Python execution failed"
        )

    def _validate_workspace_layout(self) -> None:
        root_path = self.workspace.root
        root = root_path.resolve()
        scripts = self.workspace.working / "scripts"
        if (
            root_path.is_symlink()
            or not root.is_dir()
            or self.workspace.working.is_symlink()
            or not self.workspace.working.is_dir()
            or scripts.is_symlink()
            or not scripts.is_dir()
            or self.workspace.working.resolve().parent != root
            or scripts.resolve().parent != self.workspace.working.resolve()
        ):
            raise ValueError("workspace does not have a safe working/scripts layout")

    @staticmethod
    def _validate_script_id(script_id: str) -> None:
        if not isinstance(script_id, str) or not _SCRIPT_ID_PATTERN.fullmatch(
            script_id
        ):
            raise ValueError(
                "script_id must start with a letter or digit and contain only "
                "letters, digits, underscores, or hyphens"
            )
