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
    evaluate_manifest,
    load_manifest,
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
    manifest_path = args.manifest.expanduser().resolve()
    try:
        manifest = load_manifest(manifest_path)
        rules = {
            (record.scenario_id, record.scenario_version): rules_for_scenario(
                record.scenario_id, record.scenario_version
            )
            for record in manifest.run_records
        }
        updated, evaluations = evaluate_manifest(
            manifest,
            rules,
            workspace_base_dir=manifest_path.parent,
        )
        payload = {
            "manifest": updated.model_dump(mode="json"),
            "evaluations": [evaluation.as_dict() for evaluation in evaluations],
        }
        output = dump_stable_json(payload)
        if args.output is not None:
            output_path = args.output.expanduser().resolve()
            output_path.write_text(output, encoding="utf-8")
        else:
            print(output, end="")
    except Exception as error:  # noqa: BLE001
        print(
            f"OFFLINE MANIFEST ERROR: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2
    return 0 if all(evaluation.passed for evaluation in evaluations) else 1


if __name__ == "__main__":
    raise SystemExit(main())
