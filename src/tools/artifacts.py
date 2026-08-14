"""Safe registration and verification of reproducible workspace artifacts."""

from hashlib import sha256
from pathlib import Path, PurePosixPath

from orchestration.ledger import AnalysisLedger, LedgerConflictError
from schemas.run_state import Artifact, ArtifactKind
from tools.workspace import Workspace

_APPROVED_DIRECTORIES = ("working", "outputs")
_HASH_CHUNK_SIZE = 1024 * 1024


class ArtifactPathError(ValueError):
    """Raised when an artifact path is outside approved workspace roots."""


class ArtifactManager:
    """Register existing files from approved workspace directories."""

    def __init__(self, workspace: Workspace, ledger: AnalysisLedger) -> None:
        self.workspace = workspace
        self.ledger = ledger
        self._validate_workspace_layout()

    def register(
        self,
        path: str | Path,
        *,
        artifact_id: str,
        kind: ArtifactKind = ArtifactKind.OTHER,
        media_type: str | None = None,
        description: str | None = None,
        overwrite: bool = False,
    ) -> Artifact:
        """Register a workspace file and persist its provenance metadata.

        Registration never modifies the file. ``overwrite=True`` replaces an
        existing ledger record with the same identifier after recalculating
        the file's current checksum and size.
        """

        existing = self.ledger.get_artifact(artifact_id)
        if existing is not None and not overwrite:
            raise LedgerConflictError(f"duplicate artifact id: {artifact_id}")

        relative_path = self._normalize_relative_path(path)
        absolute_path = self._resolve_approved_file(relative_path)
        checksum, size_bytes = self._provenance(absolute_path)
        artifact = Artifact(
            id=artifact_id,
            path=relative_path,
            kind=kind,
            media_type=media_type,
            description=description,
            sha256=checksum,
            size_bytes=size_bytes,
        )

        if existing is None:
            self.ledger.add_artifact(artifact)
        else:
            self.ledger.update_artifact(artifact)
        return artifact

    def register_artifact(
        self,
        path: str | Path,
        *,
        artifact_id: str,
        kind: ArtifactKind = ArtifactKind.OTHER,
        media_type: str | None = None,
        description: str | None = None,
        overwrite: bool = False,
    ) -> Artifact:
        """Descriptive alias for :meth:`register`."""

        return self.register(
            path,
            artifact_id=artifact_id,
            kind=kind,
            media_type=media_type,
            description=description,
            overwrite=overwrite,
        )

    def verify_artifact(self, artifact_id: str) -> bool:
        """Verify a registered file still matches its persisted provenance."""

        artifact = self.ledger.get_artifact(artifact_id)
        if artifact is None:
            raise KeyError(f"unknown artifact: {artifact_id}")
        absolute_path = self._resolve_approved_file(artifact.path)
        checksum, size_bytes = self._provenance(absolute_path)
        return checksum == artifact.sha256 and size_bytes == artifact.size_bytes

    def _validate_workspace_layout(self) -> None:
        root_path = self.workspace.root
        root = root_path.resolve()
        if root_path.is_symlink() or not root.is_dir():
            raise ArtifactPathError("workspace root must be a real directory")

        for directory_name in _APPROVED_DIRECTORIES:
            directory = getattr(self.workspace, directory_name)
            if (
                directory.is_symlink()
                or not directory.is_dir()
                or directory.resolve().parent != root
            ):
                raise ArtifactPathError(
                    f"workspace {directory_name} directory is not safely rooted"
                )

    def _normalize_relative_path(self, path: str | Path) -> str:
        if not isinstance(path, (str, Path)):
            raise ArtifactPathError("artifact path must be a string or Path")

        raw_path = str(path)
        normalized = raw_path.replace("\\", "/")
        pure_path = PurePosixPath(normalized)
        if (
            not pure_path.parts
            or pure_path.is_absolute()
            or ".." in pure_path.parts
            or normalized.startswith("./")
            or (len(normalized) > 1 and normalized[1] == ":")
        ):
            raise ArtifactPathError(
                "artifact path must be relative and cannot contain traversal"
            )
        if pure_path.parts[0] not in _APPROVED_DIRECTORIES:
            raise ArtifactPathError("artifact path must be inside working/ or outputs/")
        if len(pure_path.parts) < 2:
            raise ArtifactPathError("artifact path must identify a file")
        return pure_path.as_posix()

    def _resolve_approved_file(self, relative_path: str) -> Path:
        root = self.workspace.root.resolve()
        candidate = root.joinpath(*PurePosixPath(relative_path).parts)
        current = root
        for part in PurePosixPath(relative_path).parts:
            current /= part
            if current.is_symlink():
                raise ArtifactPathError(
                    "artifact path cannot contain symlink components"
                )

        if not candidate.exists():
            raise FileNotFoundError(f"artifact does not exist: {relative_path}")
        if not candidate.is_file():
            raise IsADirectoryError(f"artifact path is not a file: {relative_path}")

        resolved_candidate = candidate.resolve(strict=True)
        approved_root = getattr(self.workspace, relative_path.split("/", 1)[0])
        try:
            resolved_candidate.relative_to(approved_root.resolve())
        except ValueError as exc:
            raise ArtifactPathError(
                "artifact path resolves outside its approved directory"
            ) from exc
        return candidate

    @staticmethod
    def _provenance(path: Path) -> tuple[str, int]:
        digest = sha256()
        size_bytes = 0
        with path.open("rb") as artifact_file:
            while chunk := artifact_file.read(_HASH_CHUNK_SIZE):
                digest.update(chunk)
                size_bytes += len(chunk)
        return digest.hexdigest(), size_bytes
