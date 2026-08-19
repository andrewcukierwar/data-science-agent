"""Regression tests for non-destructive offline output publication."""

from pathlib import Path

import pytest

from evaluation.output import (
    OfflineOutputError,
    ensure_distinct_paths,
    write_exclusive_text,
)


def test_input_aliases_are_rejected_for_relative_and_symlink_paths(
    tmp_path: Path,
) -> None:
    source = tmp_path / "manifest.json"
    source.write_text("source\n", encoding="utf-8")

    relative_alias = tmp_path / "nested" / ".." / "manifest.json"
    with pytest.raises(OfflineOutputError, match="must differ from input"):
        ensure_distinct_paths(source, relative_alias)

    symlink_alias = tmp_path / "manifest-alias.json"
    symlink_alias.symlink_to(source)
    with pytest.raises(OfflineOutputError, match="must differ from input"):
        ensure_distinct_paths(source, symlink_alias)


def test_exclusive_writer_publishes_once_and_leaves_no_partial_output(
    tmp_path: Path,
) -> None:
    output = tmp_path / "nested" / "report.json"

    published = write_exclusive_text(output, '{"status":"pass"}\n')

    assert published == output.resolve()
    assert output.read_text(encoding="utf-8") == '{"status":"pass"}\n'
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))

    with pytest.raises(OfflineOutputError, match="refusing to overwrite"):
        write_exclusive_text(output, '{"status":"changed"}\n')
    assert output.read_text(encoding="utf-8") == '{"status":"pass"}\n'


def test_exclusive_writer_rejects_existing_symlink_destination(tmp_path: Path) -> None:
    existing = tmp_path / "existing.json"
    existing.write_text("keep\n", encoding="utf-8")
    destination = tmp_path / "destination.json"
    destination.symlink_to(existing)

    with pytest.raises(OfflineOutputError, match="refusing to overwrite"):
        write_exclusive_text(destination, "replace\n")
    assert existing.read_text(encoding="utf-8") == "keep\n"
