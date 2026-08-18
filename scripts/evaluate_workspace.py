#!/usr/bin/env python3
"""Evaluate one persisted workspace without loading agents or calling APIs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
for import_root in (REPOSITORY_ROOT / "src", REPOSITORY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from evaluation.engine import dump_stable_json, evaluate_workspace  # noqa: E402
from evaluation.rules import rules_for_scenario  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument(
        "--scenario-id",
        default="canonical-q2-profitability",
        help="Registered deterministic scenario evaluator to use.",
    )
    parser.add_argument(
        "--scenario-version",
        help="Optional registered scenario version; required when versions branch.",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        evaluation = evaluate_workspace(
            args.workspace,
            rules_for_scenario(args.scenario_id, args.scenario_version),
        )
    except Exception as error:  # noqa: BLE001
        print(
            f"OFFLINE EVALUATION ERROR: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2
    print(dump_stable_json(evaluation.as_dict()), end="")
    return 0 if evaluation.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
