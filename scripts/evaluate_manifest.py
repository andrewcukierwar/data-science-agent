#!/usr/bin/env python3
"""Offline-rescore every persisted workspace referenced by a benchmark manifest."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
for import_root in (REPOSITORY_ROOT / "src", REPOSITORY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from evaluation.engine import (  # noqa: E402
    dump_stable_json,
    load_manifest,
    rescore_manifest,
)
from evaluation.output import (  # noqa: E402
    canonical_path,
    ensure_distinct_paths,
    ensure_output_is_new,
    write_exclusive_text,
)
from evaluation.rules import rules_for_scenario  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional new output path; the input manifest is never overwritten.",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        manifest_path = canonical_path(args.manifest)
        output_path = None
        if args.output is not None:
            _, output_path = ensure_distinct_paths(manifest_path, args.output)
            ensure_output_is_new(args.output)
        manifest = load_manifest(manifest_path)
        rules = {
            (reference.scenario_id, reference.scenario_version): rules_for_scenario(
                reference.scenario_id, reference.scenario_version
            )
            for reference in manifest.scenario_references
        }
        updated, evaluations = rescore_manifest(
            manifest,
            rules,
            workspace_base_dir=manifest_path.parent,
        )
        payload = {
            "manifest": updated.model_dump(mode="json"),
            "evaluations": [evaluation.as_dict() for evaluation in evaluations],
        }
        output = dump_stable_json(payload)
        if output_path is not None:
            write_exclusive_text(output_path, output)
        else:
            print(output, end="")
    except Exception as error:  # noqa: BLE001
        print(
            f"OFFLINE MANIFEST ERROR: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2
    return (
        0
        if all(
            record.evaluator_result.status.value == "pass"
            for record in updated.run_records
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
