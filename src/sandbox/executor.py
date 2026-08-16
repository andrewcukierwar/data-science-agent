"""Docker-backed isolated execution for analysis scripts."""

from __future__ import annotations

import math
import os
import re
import subprocess
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from tools.workspace import Workspace

_CONTAINER_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_MEMORY_PATTERN = re.compile(r"[1-9][0-9]*[bkmgBKMG]?\Z")
_USER_PATTERN = re.compile(r"([1-9][0-9]*):([1-9][0-9]*)\Z")


class SandboxPathError(ValueError):
    """Raised when a script path is outside the approved script directory."""


class SandboxExecutionResult(BaseModel):
    """Captured result of one isolated Docker execution."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_seconds: float = Field(ge=0)
    timed_out: bool = False
    error: str | None = None


class DockerSandboxExecutor:
    """Execute approved workspace scripts in a constrained Docker container."""

    def __init__(
        self,
        workspace: Workspace,
        *,
        image: str = "data-science-agent-python:latest",
        memory_limit: str = "512m",
        cpu_limit: float = 1.0,
        pids_limit: int = 128,
        timeout_seconds: float = 30.0,
        container_user: str | None = None,
        docker_binary: str = "docker",
    ) -> None:
        self.workspace = workspace
        self.image = image
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.pids_limit = pids_limit
        self.timeout_seconds = timeout_seconds
        self.container_user = container_user or self._default_container_user()
        self.docker_binary = docker_binary
        self._validate_configuration()
        self._validate_workspace_layout()

    def execute(
        self,
        script_path: str | Path,
        *,
        timeout_seconds: float | None = None,
    ) -> SandboxExecutionResult:
        """Execute a validated workspace script and capture its process state."""

        relative_path = self._validate_script_path(script_path)
        timeout = self._validated_timeout(timeout_seconds)
        container_name = f"dsa-python-{uuid.uuid4().hex}"
        command = self.build_command(
            relative_path,
            container_name=container_name,
        )
        started = time.monotonic()

        try:
            completed = self._run_with_mount_retry(command, timeout)
        except subprocess.TimeoutExpired as exc:
            self._remove_container(container_name)
            return SandboxExecutionResult(
                success=False,
                stdout=self._as_text(exc.stdout),
                stderr=self._as_text(exc.stderr),
                duration_seconds=time.monotonic() - started,
                timed_out=True,
                error=f"execution timed out after {timeout:g} seconds",
            )
        except OSError as exc:
            return SandboxExecutionResult(
                success=False,
                duration_seconds=time.monotonic() - started,
                error=f"could not start Docker execution: {type(exc).__name__}: {exc}",
            )

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode == 0:
            return SandboxExecutionResult(
                success=True,
                stdout=stdout,
                stderr=stderr,
                exit_code=completed.returncode,
                duration_seconds=time.monotonic() - started,
            )

        return SandboxExecutionResult(
            success=False,
            stdout=stdout,
            stderr=stderr,
            exit_code=completed.returncode,
            duration_seconds=time.monotonic() - started,
            error=stderr.strip()
            or f"Docker execution exited with code {completed.returncode}",
        )

    def _run_with_mount_retry(
        self,
        command: list[str],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        """Retry transient Docker Desktop bind propagation failures."""

        for attempt in range(3):
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
            if (
                completed.returncode != 125
                or "invalid mount config" not in (completed.stderr or "").lower()
                or attempt == 2
            ):
                return completed
            time.sleep(0.1)
        raise RuntimeError("unreachable Docker retry state")

    def build_command(
        self,
        script_path: str | Path,
        *,
        container_name: str = "dsa-python-command",
    ) -> list[str]:
        """Build the argument-vector used for one isolated Docker run."""

        relative_path = self._validate_script_path(script_path)
        if not _CONTAINER_NAME_PATTERN.fullmatch(container_name):
            raise ValueError("container_name contains unsafe characters")

        return [
            self.docker_binary,
            "run",
            "--rm",
            "--init",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            str(self.pids_limit),
            "--memory",
            self.memory_limit,
            "--cpus",
            str(self.cpu_limit),
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--user",
            self.container_user,
            "--mount",
            # Mount the run root read-only, then overlay only the two approved
            # writable directories. This preserves /workspace/inputs and
            # /workspace/docs while avoiding Docker Desktop's rejection of
            # direct read-only binds for nested host directories.
            self._mount(self.workspace.root, "/workspace", "readonly"),
            "--mount",
            self._mount(self.workspace.working, "/workspace/working", "rw"),
            "--mount",
            self._mount(self.workspace.outputs, "/workspace/outputs", "rw"),
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONUNBUFFERED=1",
            "--env",
            "MPLCONFIGDIR=/tmp/matplotlib",
            "--workdir",
            "/workspace",
            self.image,
            "python",
            "-B",
            f"/workspace/{relative_path}",
        ]

    def _validate_configuration(self) -> None:
        if (
            not isinstance(self.image, str)
            or not self.image
            or any(character.isspace() for character in self.image)
        ):
            raise ValueError("image must be a non-empty Docker image reference")
        if not _MEMORY_PATTERN.fullmatch(self.memory_limit):
            raise ValueError("memory_limit must be a Docker memory value")
        if not math.isfinite(self.cpu_limit) or self.cpu_limit <= 0:
            raise ValueError("cpu_limit must be a positive finite number")
        if not isinstance(self.pids_limit, int) or self.pids_limit < 1:
            raise ValueError("pids_limit must be a positive integer")
        self._validated_timeout(self.timeout_seconds)
        if not _USER_PATTERN.fullmatch(self.container_user):
            raise ValueError("container_user must contain non-root uid:gid values")
        if not self.docker_binary or any(
            character.isspace() for character in self.docker_binary
        ):
            raise ValueError("docker_binary must be a non-empty executable name")

    def _validate_workspace_layout(self) -> None:
        root_path = self.workspace.root
        root = root_path.resolve()
        if root_path.is_symlink() or not root.is_dir():
            raise SandboxPathError("workspace root must be a real directory")

        for directory_name in ("inputs", "docs", "working", "outputs"):
            directory = getattr(self.workspace, directory_name)
            if (
                directory.is_symlink()
                or not directory.is_dir()
                or directory.resolve().parent != root
            ):
                raise SandboxPathError(
                    f"workspace {directory_name} directory is not safely rooted"
                )

        scripts = self.workspace.working / "scripts"
        if (
            scripts.is_symlink()
            or not scripts.is_dir()
            or scripts.resolve().parent != self.workspace.working.resolve()
        ):
            raise SandboxPathError("workspace working/scripts is not safely rooted")

    def _validate_script_path(self, script_path: str | Path) -> str:
        if not isinstance(script_path, (str, Path)):
            raise SandboxPathError("script path must be a string or Path")

        normalized = str(script_path).replace("\\", "/")
        pure_path = PurePosixPath(normalized)
        if (
            not pure_path.parts
            or pure_path.is_absolute()
            or ".." in pure_path.parts
            or normalized.startswith("./")
            or (len(normalized) > 1 and normalized[1] == ":")
            or pure_path.parts[:2] != ("working", "scripts")
            or len(pure_path.parts) < 3
            or pure_path.suffix.lower() != ".py"
        ):
            raise SandboxPathError(
                "script path must be a relative .py file under working/scripts"
            )

        root = self.workspace.root.resolve()
        candidate = root.joinpath(*pure_path.parts)
        current = root
        for part in pure_path.parts:
            current /= part
            if current.is_symlink():
                raise SandboxPathError("script path cannot contain symlink components")

        if not candidate.exists():
            raise FileNotFoundError(f"script does not exist: {pure_path.as_posix()}")
        if not candidate.is_file():
            raise IsADirectoryError(f"script path is not a file: {pure_path}")

        scripts_root = (self.workspace.working / "scripts").resolve()
        try:
            candidate.resolve(strict=True).relative_to(scripts_root)
        except ValueError as exc:
            raise SandboxPathError(
                "script path resolves outside working/scripts"
            ) from exc
        return pure_path.as_posix()

    def _validated_timeout(self, timeout_seconds: float | None) -> float:
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout):
            raise ValueError("timeout_seconds must be a finite number")
        if timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        return float(timeout)

    def _remove_container(self, container_name: str) -> None:
        try:
            subprocess.run(
                [self.docker_binary, "rm", "--force", container_name],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return

    @staticmethod
    def _mount(directory: Path, destination: str, mode: str) -> str:
        # Docker's long-form --mount syntax accepts `readonly` as a flag, but
        # does not accept the short `rw` flag. Read-write is the default when
        # no mode is supplied, so omit it for writable workspace mounts. The
        # trailing source slash keeps Docker Desktop compatible with host
        # directories that are themselves read-only.
        suffix = ",readonly" if mode == "readonly" else ""
        source = str(directory.resolve())
        if mode == "readonly":
            source += "/"
        return f"type=bind,src={source},dst={destination}{suffix}"

    @staticmethod
    def _default_container_user() -> str:
        try:
            uid = os.getuid()
            gid = os.getgid()
        except AttributeError:
            uid = gid = 1000
        if uid == 0 or gid == 0:
            uid = gid = 1000
        return f"{uid}:{gid}"

    @staticmethod
    def _as_text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode(errors="replace")
        return value
