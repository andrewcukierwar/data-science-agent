"""Persisted cryptographic identity for benchmark-generated workspaces."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from evaluation.contracts import SourceFileIdentity, WorkspaceIdentity
from tools.workspace import Workspace

WORKSPACE_IDENTITY_FILENAME = "workspace_identity.json"
_HASH_CHUNK_SIZE = 1024 * 1024


class WorkspaceIdentityError(ValueError):
    """Raised when a persisted workspace identity is absent or inconsistent."""


def _workspace_root(workspace: Workspace | str | Path) -> Path:
    if isinstance(workspace, Workspace):
        return workspace.root.resolve()
    path = Path(workspace).expanduser().resolve()
    if path.name == "analysis_ledger.json":
        return path.parent.parent
    if path.name == "state":
        return path.parent
    return path


def workspace_identity_path(workspace: Workspace | str | Path) -> Path:
    """Return the immutable identity file inside a workspace state directory."""

    return _workspace_root(workspace) / "state" / WORKSPACE_IDENTITY_FILENAME


def _file_identity(path: Path, relative_path: str) -> SourceFileIdentity:
    if path.is_symlink() or not path.is_file():
        raise WorkspaceIdentityError(f"workspace source is not a regular file: {path}")
    digest = sha256()
    size_bytes = 0
    try:
        with path.open("rb") as source_file:
            while chunk := source_file.read(_HASH_CHUNK_SIZE):
                digest.update(chunk)
                size_bytes += len(chunk)
    except OSError as exc:
        raise WorkspaceIdentityError(
            f"workspace source cannot be read: {path}"
        ) from exc
    return SourceFileIdentity(
        path=relative_path,
        sha256=digest.hexdigest(),
        size_bytes=size_bytes,
    )


def source_file_identities_for_roots(
    inputs_root: str | Path,
    docs_root: str | Path,
) -> tuple[SourceFileIdentity, ...]:
    """Hash all deterministic source files using workspace-relative paths."""

    identities: list[SourceFileIdentity] = []
    for prefix, root_value in (
        ("inputs", inputs_root),
        ("docs", docs_root),
    ):
        root = Path(root_value).resolve()
        if not root.is_dir():
            raise WorkspaceIdentityError(
                f"workspace source directory is missing: {root}"
            )
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            if path.is_symlink():
                raise WorkspaceIdentityError(
                    f"workspace source tree contains a symlink: {path}"
                )
            if path.is_file():
                relative = f"{prefix}/{path.relative_to(root).as_posix()}"
                identities.append(_file_identity(path, relative))
    if not identities:
        raise WorkspaceIdentityError("workspace source bundle contains no files")
    return tuple(identities)


def source_file_identities(
    workspace: Workspace | str | Path,
) -> tuple[SourceFileIdentity, ...]:
    """Hash the inputs and docs currently persisted in a workspace."""

    root = _workspace_root(workspace)
    return source_file_identities_for_roots(root / "inputs", root / "docs")


def load_workspace_identity(workspace: Workspace | str | Path) -> WorkspaceIdentity:
    """Load and validate the persisted workspace identity."""

    path = workspace_identity_path(workspace)
    if not path.exists():
        raise WorkspaceIdentityError(f"workspace identity is missing: {path}")
    if path.is_symlink() or not path.is_file():
        raise WorkspaceIdentityError(
            f"workspace identity is not a regular file: {path}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkspaceIdentityError(f"workspace identity is missing: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceIdentityError(
            f"workspace identity cannot be read as JSON: {path}"
        ) from exc
    try:
        return WorkspaceIdentity.model_validate(payload)
    except ValueError as exc:
        raise WorkspaceIdentityError(
            f"workspace identity is invalid: {path}: {exc}"
        ) from exc


def persist_workspace_identity(
    workspace: Workspace | str | Path,
    identity: WorkspaceIdentity,
) -> Path:
    """Write one identity record exclusively after validating source hashes."""

    root = _workspace_root(workspace)
    current_sources = source_file_identities(root)
    if current_sources != identity.source_files:
        raise WorkspaceIdentityError(
            "workspace source files do not match the identity being persisted"
        )
    path = workspace_identity_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            identity.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    )
    try:
        with path.open("x", encoding="utf-8", newline="\n") as identity_file:
            identity_file.write(payload)
    except FileExistsError as exc:
        try:
            existing = load_workspace_identity(root)
        except WorkspaceIdentityError:
            raise
        if existing != identity:
            raise WorkspaceIdentityError(
                f"workspace identity already exists and differs: {path}"
            ) from exc
    return path


def verify_workspace_identity(
    workspace: Workspace | str | Path,
    expected: WorkspaceIdentity,
) -> WorkspaceIdentity:
    """Refuse a workspace whose identity or source files differ from expected."""

    actual = load_workspace_identity(workspace)
    if actual != expected:
        raise WorkspaceIdentityError(
            "workspace identity does not match the benchmark manifest"
        )
    current_sources = source_file_identities(workspace)
    if current_sources != actual.source_files:
        raise WorkspaceIdentityError(
            "workspace source files no longer match persisted identity hashes"
        )
    return actual


def verify_workspace_identity_integrity(
    workspace: Workspace | str | Path,
) -> WorkspaceIdentity:
    """Verify a workspace's persisted identity against its current source files."""

    identity = load_workspace_identity(workspace)
    current_sources = source_file_identities(workspace)
    if current_sources != identity.source_files:
        raise WorkspaceIdentityError(
            "workspace source files no longer match persisted identity hashes"
        )
    return identity


def verify_identity_matches_rules(
    identity: WorkspaceIdentity,
    *,
    scenario_id: str,
    scenario_version: str,
    evaluator_version: str,
) -> WorkspaceIdentity:
    """Require selected evaluator rules to match a persisted identity."""

    expected = {
        "scenario_id": scenario_id,
        "scenario_version": scenario_version,
        "evaluator_version": evaluator_version,
    }
    actual = {name: getattr(identity, name) for name in expected}
    mismatches = [
        f"{name}={actual[name]!r} (rules require {expected[name]!r})"
        for name in expected
        if actual[name] != expected[name]
    ]
    if mismatches:
        raise WorkspaceIdentityError(
            "workspace identity does not match evaluator rules: "
            + ", ".join(mismatches)
        )
    return identity


def verify_workspace_identity_for_rules(
    workspace: Workspace | str | Path,
    *,
    scenario_id: str,
    scenario_version: str,
    evaluator_version: str,
) -> WorkspaceIdentity:
    """Verify source integrity and that persisted identity selects these rules."""

    identity = verify_workspace_identity_integrity(workspace)
    return verify_identity_matches_rules(
        identity,
        scenario_id=scenario_id,
        scenario_version=scenario_version,
        evaluator_version=evaluator_version,
    )


__all__ = [
    "WORKSPACE_IDENTITY_FILENAME",
    "WorkspaceIdentityError",
    "load_workspace_identity",
    "persist_workspace_identity",
    "source_file_identities",
    "source_file_identities_for_roots",
    "verify_identity_matches_rules",
    "verify_workspace_identity",
    "verify_workspace_identity_for_rules",
    "verify_workspace_identity_integrity",
    "workspace_identity_path",
]
