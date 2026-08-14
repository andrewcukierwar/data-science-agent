"""Unit tests for safe workspace artifact registration."""

from hashlib import sha256
from pathlib import Path

import pytest

from orchestration.ledger import AnalysisLedger, LedgerConflictError
from schemas.run_state import ArtifactKind
from tools.artifacts import ArtifactManager, ArtifactPathError
from tools.workspace import Workspace, WorkspaceManager


def _artifact_manager(
    tmp_path: Path,
) -> tuple[Workspace, AnalysisLedger, ArtifactManager]:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-artifacts"
    )
    ledger = AnalysisLedger(workspace, objective="Explain the order trend.")
    return workspace, ledger, ArtifactManager(workspace, ledger)


def test_registers_artifact_with_relative_path_and_provenance(
    tmp_path: Path,
) -> None:
    workspace, ledger, manager = _artifact_manager(tmp_path)
    content = b"SELECT order_id FROM orders;\n"
    artifact_path = workspace.working / "queries" / "orders.sql"
    artifact_path.write_bytes(content)

    artifact = manager.register_artifact(
        "working/queries/orders.sql",
        artifact_id="Q001",
        kind=ArtifactKind.QUERY,
        media_type="text/sql",
        description="Select the order identifiers.",
    )

    assert artifact.path == "working/queries/orders.sql"
    assert not Path(artifact.path).is_absolute()
    assert artifact.sha256 == sha256(content).hexdigest()
    assert artifact.size_bytes == len(content)
    assert manager.verify_artifact("Q001") is True

    reloaded = AnalysisLedger(ledger.state_path)
    persisted = reloaded.get_artifact("Q001")
    assert persisted is not None
    assert persisted.path == "working/queries/orders.sql"
    assert persisted.sha256 == artifact.sha256
    assert persisted.size_bytes == artifact.size_bytes


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/outside.txt",
        "../outside.txt",
        "working/../outside.txt",
        "working\\..\\inputs\\secret.txt",
        "inputs/source.csv",
        "docs/notes.md",
        "working",
    ],
)
def test_rejects_paths_outside_approved_artifact_directories(
    tmp_path: Path,
    path: str,
) -> None:
    _, _, manager = _artifact_manager(tmp_path)

    with pytest.raises(ArtifactPathError):
        manager.register(path, artifact_id="A001")


def test_rejects_absolute_path_even_when_file_is_inside_workspace(
    tmp_path: Path,
) -> None:
    workspace, _, manager = _artifact_manager(tmp_path)
    artifact_path = workspace.outputs / "report.md"
    artifact_path.write_text("report", encoding="utf-8")

    with pytest.raises(ArtifactPathError):
        manager.register(artifact_path, artifact_id="A001")


def test_rejects_symlinked_artifact_file_that_escapes_workspace(
    tmp_path: Path,
) -> None:
    workspace, _, manager = _artifact_manager(tmp_path)
    outside_path = tmp_path / "outside.txt"
    outside_path.write_text("outside", encoding="utf-8")
    symlink_path = workspace.outputs / "charts" / "escaped.png"
    try:
        symlink_path.symlink_to(outside_path)
    except OSError:
        pytest.skip("the current platform does not permit symlink creation")

    with pytest.raises(ArtifactPathError):
        manager.register("outputs/charts/escaped.png", artifact_id="A001")


def test_rejects_symlinked_directory_component(
    tmp_path: Path,
) -> None:
    workspace, _, manager = _artifact_manager(tmp_path)
    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    (outside_directory / "report.html").write_text("report", encoding="utf-8")
    symlink_directory = workspace.outputs / "linked"
    try:
        symlink_directory.symlink_to(outside_directory, target_is_directory=True)
    except OSError:
        pytest.skip("the current platform does not permit symlink creation")

    with pytest.raises(ArtifactPathError):
        manager.register("outputs/linked/report.html", artifact_id="A001")


def test_duplicate_identifier_requires_explicit_overwrite(
    tmp_path: Path,
) -> None:
    workspace, ledger, manager = _artifact_manager(tmp_path)
    first_path = workspace.working / "scripts" / "first.py"
    second_path = workspace.outputs / "report.html"
    first_path.write_text("print('first')\n", encoding="utf-8")
    second_path.write_text("<h1>second</h1>\n", encoding="utf-8")

    first = manager.register(
        "working/scripts/first.py",
        artifact_id="A001",
        kind=ArtifactKind.SCRIPT,
    )

    with pytest.raises(LedgerConflictError):
        manager.register("outputs/report.html", artifact_id="A001")

    assert ledger.get_artifact("A001") == first

    replacement = manager.register(
        "outputs/report.html",
        artifact_id="A001",
        kind=ArtifactKind.REPORT,
        overwrite=True,
    )

    assert replacement.path == "outputs/report.html"
    assert replacement.kind is ArtifactKind.REPORT
    assert ledger.get_artifact("A001") == replacement


@pytest.mark.parametrize(
    ("relative_path", "kind"),
    [
        ("working/queries/query.sql", ArtifactKind.QUERY),
        ("working/scripts/script.py", ArtifactKind.SCRIPT),
        ("outputs/charts/chart.png", ArtifactKind.CHART),
        ("outputs/report.md", ArtifactKind.REPORT),
        ("outputs/other.bin", ArtifactKind.OTHER),
    ],
)
def test_supports_all_declared_artifact_kinds(
    tmp_path: Path,
    relative_path: str,
    kind: ArtifactKind,
) -> None:
    workspace, _, manager = _artifact_manager(tmp_path)
    path = workspace.root / relative_path
    path.write_bytes(b"artifact")

    artifact = manager.register(relative_path, artifact_id=kind.value, kind=kind)

    assert artifact.kind is kind
    assert manager.verify_artifact(kind.value)


def test_verification_detects_file_changes(tmp_path: Path) -> None:
    workspace, _, manager = _artifact_manager(tmp_path)
    artifact_path = workspace.outputs / "report.md"
    artifact_path.write_text("original", encoding="utf-8")
    manager.register("outputs/report.md", artifact_id="A001", kind=ArtifactKind.REPORT)

    artifact_path.write_text("changed", encoding="utf-8")

    assert manager.verify_artifact("A001") is False
