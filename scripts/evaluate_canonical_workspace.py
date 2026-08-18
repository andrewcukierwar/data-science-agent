#!/usr/bin/env python3
"""Evaluate one persisted canonical workspace without running any agents."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
for import_root in (REPOSITORY_ROOT / "src", REPOSITORY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from evaluation.canonical import (  # noqa: E402
    CanonicalAcceptanceError,
    evaluate_canonical_workspace,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "workspace",
        type=Path,
        help="Path to an existing completed run workspace.",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        summary = evaluate_canonical_workspace(args.workspace)
    except CanonicalAcceptanceError as error:
        print(f"CANONICAL ACCEPTANCE FAILED: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary.as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
