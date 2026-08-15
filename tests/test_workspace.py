"""Unit tests for isolated analysis workspace lifecycle operations."""

import stat
from pathlib import Path

import pytest

from tools.workspace import WorkspaceManager


def _permissions(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_create_workspace_builds_expected_layout_and_permissions(
    tmp_path: Path,
) -> None:
    inputs_source = tmp_path / "input-source"
    docs_source = tmp_path / "docs-source"
    inputs_source.mkdir()
    docs_source.mkdir()
    (inputs_source / "orders.parquet").write_text("input", encoding="utf-8")
    (docs_source / "business_definitions.md").write_text(
        "definitions", encoding="utf-8"
    )

    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-001",
        inputs_source=inputs_source,
        docs_source=docs_source,
    )

    expected_directories = (
        workspace.root,
        workspace.inputs,
        workspace.docs,
        workspace.working,
        workspace.working / "queries",
        workspace.working / "scripts",
        workspace.outputs,
        workspace.outputs / "charts",
        workspace.state,
        workspace.logs,
    )
    assert all(path.is_dir() for path in expected_directories)
    assert (workspace.inputs / "orders.parquet").read_text(encoding="utf-8") == "input"
    assert (workspace.docs / "business_definitions.md").read_text(
        encoding="utf-8"
    ) == "definitions"

    assert _permissions(workspace.root) == 0o755
    assert _permissions(workspace.inputs) == 0o555
    assert _permissions(workspace.docs) == 0o555
    assert _permissions(workspace.inputs / "orders.parquet") == 0o444
    assert _permissions(workspace.docs / "business_definitions.md") == 0o444
    assert _permissions(workspace.working) == 0o755
    assert _permissions(workspace.outputs) == 0o755
    assert _permissions(workspace.state) == 0o755
    assert _permissions(workspace.logs) == 0o755


def test_working_and_output_directories_are_writable(tmp_path: Path) -> None:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace("run-002")

    query = workspace.working / "queries" / "analysis.sql"
    report = workspace.outputs / "report.md"
    query.write_text("select 1", encoding="utf-8")
    report.write_text("# Report", encoding="utf-8")

    assert query.read_text(encoding="utf-8") == "select 1"
    assert report.read_text(encoding="utf-8") == "# Report"


def test_create_rejects_duplicate_and_unsafe_run_ids(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")
    manager.create_workspace("run-003")

    with pytest.raises(FileExistsError):
        manager.create_workspace("run-003")

    with pytest.raises(ValueError):
        manager.create_workspace("../outside")
    with pytest.raises(ValueError):
        manager.create_workspace("")


def test_cleanup_is_scoped_and_idempotent(tmp_path: Path) -> None:
    base_dir = tmp_path / "workspaces"
    manager = WorkspaceManager(base_dir)
    workspace = manager.create_workspace("run-004")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")

    assert manager.cleanup_workspace("run-004") is True
    assert not workspace.root.exists()
    assert manager.cleanup_workspace("run-004") is False
    assert outside.read_text(encoding="utf-8") == "keep"
    assert base_dir.is_dir()


def test_cleanup_removes_workspace_with_read_only_sources(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "input.parquet").write_text("input", encoding="utf-8")
    manager = WorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create_workspace("run-read-only", inputs_source=source)

    assert _permissions(workspace.inputs) == 0o555
    assert manager.cleanup_workspace("run-read-only") is True
    assert not workspace.root.exists()


def test_cleanup_refuses_symlinked_run_directory(tmp_path: Path) -> None:
    base_dir = tmp_path / "workspaces"
    manager = WorkspaceManager(base_dir)
    outside = tmp_path / "outside"
    outside.mkdir()
    (base_dir / "run-005").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError):
        manager.cleanup_workspace("run-005")

    assert outside.is_dir()
