"""Deterministic writers for generated scenario source files."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pandas as pd


class SourceWriteError(ValueError):
    """Raised when a source bundle cannot be written safely."""


def write_deterministic_sources(
    output_dir: str | Path,
    tables: Mapping[str, pd.DataFrame],
    documents: Mapping[str, str],
    *,
    table_filenames: Mapping[str, str] | None = None,
    document_filenames: Mapping[str, str] | None = None,
    overwrite: bool = False,
) -> dict[str, Path]:
    """Write a named source bundle in a stable order and file format.

    The writer accepts only in-memory typed table/document values, uses fixed
    parquet options, and returns paths keyed by logical source name.  Stable
    ordering and explicit filenames keep generated source trees reproducible
    across repeated runs with the same seed and configuration.
    """

    destination = Path(output_dir).expanduser().resolve()
    table_filenames = table_filenames or {name: f"{name}.parquet" for name in tables}
    document_filenames = document_filenames or {
        name: f"{name}.md" for name in documents
    }
    unknown_tables = set(tables) - set(table_filenames)
    unknown_documents = set(documents) - set(document_filenames)
    if unknown_tables or unknown_documents:
        raise SourceWriteError(
            "source filename mappings are incomplete for: "
            + ", ".join(sorted((*unknown_tables, *unknown_documents)))
        )

    relative_filenames = {
        **{name: Path(table_filenames[name]) for name in sorted(tables)},
        **{name: Path(document_filenames[name]) for name in sorted(documents)},
    }
    if any(path.is_absolute() for path in relative_filenames.values()):
        raise SourceWriteError("source filenames must be relative")
    if any(".." in path.parts for path in relative_filenames.values()):
        raise SourceWriteError("source filenames cannot contain traversal")

    paths = {
        **{name: destination / relative_filenames[name] for name in sorted(tables)},
        **{name: destination / relative_filenames[name] for name in sorted(documents)},
    }
    if len(set(paths.values())) != len(paths):
        raise SourceWriteError("source filenames must be unique")

    destination.mkdir(parents=True, exist_ok=True)
    existing = [path for path in paths.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "dataset output already exists: "
            + ", ".join(str(path) for path in sorted(existing))
        )

    for name in sorted(tables):
        frame = tables[name]
        if not isinstance(frame, pd.DataFrame):
            raise SourceWriteError(f"table {name!r} is not a pandas DataFrame")
        frame.to_parquet(
            paths[name],
            index=False,
            compression="snappy",
            engine="pyarrow",
        )
    for name in sorted(documents):
        paths[name].write_text(documents[name], encoding="utf-8", newline="\n")
    return paths


__all__ = ["SourceWriteError", "write_deterministic_sources"]
