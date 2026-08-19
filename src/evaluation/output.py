"""Exclusive, atomic writers for deterministic offline artifacts."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class OfflineOutputError(RuntimeError):
    """Raised when an offline output cannot be published safely."""


def canonical_path(path: str | Path) -> Path:
    """Return a normalized path while allowing a not-yet-created destination."""

    return Path(path).expanduser().resolve(strict=False)


def ensure_distinct_paths(
    input_path: str | Path,
    output_path: str | Path,
) -> tuple[Path, Path]:
    """Reject an output that aliases the source through path or filesystem links."""

    source = canonical_path(input_path)
    destination = canonical_path(output_path)
    same_file = source == destination
    if not same_file and source.exists() and destination.exists():
        try:
            same_file = os.path.samefile(source, destination)
        except OSError:
            same_file = False
    if same_file:
        raise OfflineOutputError(
            f"offline output must differ from input: {destination}"
        )
    return source, destination


def ensure_output_is_new(path: str | Path) -> Path:
    """Reject a destination that already exists, including a dangling link."""

    requested = Path(path).expanduser()
    destination = canonical_path(requested)
    if os.path.lexists(requested) or os.path.lexists(destination):
        raise OfflineOutputError(
            f"refusing to overwrite existing offline output: {destination}"
        )
    return destination


def write_exclusive_text(path: str | Path, text: str) -> Path:
    """Publish text once, atomically, without replacing an existing path.

    Serialization/validation must happen before this function is called. The
    temporary file is created beside the destination, flushed, and atomically
    linked into place. A hard-link publication fails if another process creates
    the destination first, so the source and any existing output are untouched.
    """

    if not isinstance(text, str):
        raise TypeError("offline output text must be a string")
    destination = canonical_path(path)
    ensure_output_is_new(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, destination)
        except FileExistsError as exc:
            raise OfflineOutputError(
                f"refusing to overwrite existing offline output: {destination}"
            ) from exc
        except OSError as exc:
            raise OfflineOutputError(
                f"could not publish offline output atomically: {destination}"
            ) from exc
        try:
            directory_descriptor = os.open(
                destination.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
        except OSError:
            directory_descriptor = None
        if directory_descriptor is not None:
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        temporary_path.unlink(missing_ok=True)
    return destination


__all__ = [
    "OfflineOutputError",
    "canonical_path",
    "ensure_distinct_paths",
    "ensure_output_is_new",
    "write_exclusive_text",
]
