"""Unit tests for Docker sandbox command construction and execution capture."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from sandbox.executor import DockerSandboxExecutor, SandboxPathError
from tools.workspace import WorkspaceManager


def _workspace(tmp_path: Path):
    return WorkspaceManager(tmp_path / "workspaces").create_workspace("run-sandbox")


def _script(workspace, relative_path: str = "working/scripts/analysis.py") -> Path:
    path = workspace.root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("print('ok')\n", encoding="utf-8")
    return path


def test_build_command_uses_only_constrained_workspace_mounts(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    _script(workspace)
    executor = DockerSandboxExecutor(
        workspace,
        image="test-python:latest",
        memory_limit="256m",
        cpu_limit=0.5,
        pids_limit=64,
        timeout_seconds=12,
        container_user="1000:1000",
    )

    command = executor.build_command(
        "working/scripts/analysis.py",
        container_name="unit-test",
    )

    assert command[:2] == ["docker", "run"]
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert command[command.index("--user") + 1] == "1000:1000"
    assert command[command.index("--memory") + 1] == "256m"
    assert command[command.index("--cpus") + 1] == "0.5"
    assert command[command.index("--pids-limit") + 1] == "64"
    assert "MPLCONFIGDIR=/tmp/matplotlib" in command
    root_path = workspace.root.resolve()
    readonly_root_mount = f"type=bind,src={root_path}/,dst=/workspace,readonly"
    assert readonly_root_mount in command
    assert (
        f"type=bind,src={workspace.working.resolve()},dst=/workspace/working" in command
    )
    assert (
        f"type=bind,src={workspace.outputs.resolve()},dst=/workspace/outputs" in command
    )
    assert not any(
        mount.endswith(",rw") for mount in command if mount.startswith("type=bind,")
    )
    assert command[-3:] == [
        "python",
        "-B",
        "/workspace/working/scripts/analysis.py",
    ]


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/analysis.py",
        "../analysis.py",
        "working/scripts/../analysis.py",
        "working\\scripts\\..\\analysis.py",
        "inputs/analysis.py",
        "working/analysis.py",
        "working/scripts/analysis.txt",
    ],
)
def test_script_path_validation_rejects_unsafe_paths(
    tmp_path: Path,
    path: str,
) -> None:
    workspace = _workspace(tmp_path)
    executor = DockerSandboxExecutor(workspace)

    with pytest.raises(SandboxPathError):
        executor.build_command(path)


def test_script_path_validation_rejects_symlink_escape(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    link = workspace.working / "scripts" / "linked.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("the current platform does not permit symlink creation")

    executor = DockerSandboxExecutor(workspace)
    with pytest.raises(SandboxPathError):
        executor.build_command("working/scripts/linked.py")


def test_execute_captures_success_without_running_host_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    _script(workspace)
    executor = DockerSandboxExecutor(workspace)
    captured = {}

    def fake_run(command, **kwargs):  # noqa: ANN001
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="hello\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = executor.execute("working/scripts/analysis.py")

    assert result.success is True
    assert result.stdout == "hello\n"
    assert result.stderr == ""
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.duration_seconds >= 0
    assert captured["command"][0:2] == ["docker", "run"]
    assert captured["kwargs"]["timeout"] == 30.0


def test_execute_retries_transient_bind_mount_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    _script(workspace)
    executor = DockerSandboxExecutor(workspace)
    attempts = 0

    def fake_run(command, **kwargs):  # noqa: ANN001
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return SimpleNamespace(
                returncode=125,
                stdout="",
                stderr="invalid mount config for type bind",
            )
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = executor.execute("working/scripts/analysis.py")

    assert result.success is True
    assert result.stdout == "ok\n"
    assert attempts == 2


def test_execute_captures_timeout_and_attempts_container_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    _script(workspace)
    executor = DockerSandboxExecutor(workspace, timeout_seconds=0.1)
    commands = []

    def fake_run(command, **kwargs):  # noqa: ANN001
        commands.append(command)
        if command[1] == "rm":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output=b"partial")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = executor.execute("working/scripts/analysis.py")

    assert result.success is False
    assert result.stdout == "partial"
    assert result.exit_code is None
    assert result.timed_out is True
    assert result.error == "execution timed out after 0.1 seconds"
    assert commands[0][1:2] == ["run"]
    assert commands[1][1:3] == ["rm", "--force"]
