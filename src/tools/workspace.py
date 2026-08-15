"""Creation and cleanup of isolated per-run analysis workspaces."""

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_READ_ONLY_MODE = 0o555
_READ_ONLY_FILE_MODE = 0o444
_WRITABLE_MODE = 0o755


@dataclass(frozen=True, slots=True)
class Workspace:
    """Paths for one isolated analysis run."""

    root: Path
    inputs: Path
    docs: Path
    working: Path
    outputs: Path
    state: Path
    logs: Path

    @property
    def read_only_directories(self) -> tuple[Path, ...]:
        """Input directories that agents must not mutate."""

        return (self.inputs, self.docs)

    @property
    def writable_directories(self) -> tuple[Path, ...]:
        """Directories where the analysis system may write artifacts and state."""

        return (self.working, self.outputs, self.state, self.logs)


class WorkspaceManager:
    """Create and safely remove per-run workspace directories."""

    _DIRECTORIES: ClassVar[tuple[str, ...]] = (
        "inputs",
        "docs",
        "working",
        "working/queries",
        "working/scripts",
        "outputs",
        "outputs/charts",
        "state",
        "logs",
    )

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        if not self.base_dir.is_dir():
            raise NotADirectoryError(f"workspace base is not a directory: {base_dir}")

    def create_workspace(
        self,
        run_id: str,
        *,
        inputs_source: str | Path | None = None,
        docs_source: str | Path | None = None,
    ) -> Workspace:
        """Create a run workspace and optionally copy its read-only sources."""

        self._validate_run_id(run_id)
        root = self._run_path(run_id)
        root.mkdir()

        try:
            directories = {name: root / name for name in self._DIRECTORIES}
            for directory in directories.values():
                directory.mkdir(parents=True)

            if inputs_source is not None:
                self._copy_source_tree(inputs_source, directories["inputs"])
            if docs_source is not None:
                self._copy_source_tree(docs_source, directories["docs"])

            root.chmod(_WRITABLE_MODE)
            self._set_tree_mode(directories["inputs"], _READ_ONLY_MODE)
            self._set_tree_mode(directories["docs"], _READ_ONLY_MODE)
            for directory in directories.values():
                if directory not in (directories["inputs"], directories["docs"]):
                    self._set_tree_mode(directory, _WRITABLE_MODE)
        except Exception:
            shutil.rmtree(root)
            raise

        return Workspace(
            root=root,
            inputs=directories["inputs"],
            docs=directories["docs"],
            working=root / "working",
            outputs=root / "outputs",
            state=directories["state"],
            logs=directories["logs"],
        )

    def open_workspace(self, run_id: str) -> Workspace:
        """Open an existing run workspace after validating its layout."""

        self._validate_run_id(run_id)
        root = self._run_path(run_id)
        if root.is_symlink() or not root.is_dir():
            raise FileNotFoundError(f"workspace does not exist: {root}")

        directories = {name: root / name for name in self._DIRECTORIES}
        for directory in directories.values():
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError(f"workspace layout is invalid: {directory}")
        return Workspace(
            root=root,
            inputs=directories["inputs"],
            docs=directories["docs"],
            working=directories["working"],
            outputs=directories["outputs"],
            state=directories["state"],
            logs=directories["logs"],
        )

    def cleanup_workspace(self, run_id: str) -> bool:
        """Remove one run workspace, returning whether it existed."""

        self._validate_run_id(run_id)
        root = self._run_path(run_id)
        if not root.exists() and not root.is_symlink():
            return False
        if root.is_symlink() or not root.is_dir():
            raise ValueError(f"workspace path is not a directory: {root}")
        shutil.rmtree(root)
        return True

    def _run_path(self, run_id: str) -> Path:
        root = self.base_dir / run_id
        if root.parent != self.base_dir:
            raise ValueError(
                "run workspace must be a direct child of the base directory"
            )
        return root

    @staticmethod
    def _validate_run_id(run_id: str) -> None:
        if not isinstance(run_id, str) or not _RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError(
                "run_id must start with a letter or digit and contain only "
                "letters, digits, underscores, or hyphens"
            )

    @staticmethod
    def _copy_source_tree(source: str | Path, destination: Path) -> None:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_dir():
            raise NotADirectoryError(f"workspace source is not a directory: {source}")
        if any(path.is_symlink() for path in source_path.rglob("*")):
            raise ValueError(f"workspace sources cannot contain symlinks: {source}")
        shutil.copytree(source_path, destination, dirs_exist_ok=True)

    @staticmethod
    def _set_tree_mode(root: Path, directory_mode: int) -> None:
        root.chmod(directory_mode)
        for path in root.rglob("*"):
            if path.is_dir():
                path.chmod(directory_mode)
            else:
                path.chmod(
                    _READ_ONLY_FILE_MODE
                    if directory_mode == _READ_ONLY_MODE
                    else _WRITABLE_MODE
                )
