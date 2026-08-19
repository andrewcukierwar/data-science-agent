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
from evaluation.workspace_identity import (  # noqa: E402
    WorkspaceIdentityError,
    load_workspace_identity,
    workspace_identity_path,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument(
        "--scenario-id",
        help=(
            "Explicit scenario evaluator; otherwise derive it from workspace identity."
        ),
    )
    parser.add_argument(
        "--scenario-version",
        help="Optional registered scenario version; required when versions branch.",
    )
    parser.add_argument(
        "--legacy-diagnostic",
        action="store_true",
        help=(
            "Allow diagnostic-only evaluation of an unbound legacy workspace; "
            "requires explicit --scenario-id and --scenario-version."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        identity_path = workspace_identity_path(args.workspace)
        if identity_path.is_symlink() or identity_path.exists():
            identity = load_workspace_identity(args.workspace)
            scenario_id = args.scenario_id or identity.scenario_id
            scenario_version = args.scenario_version or identity.scenario_version
            if scenario_id != identity.scenario_id:
                raise WorkspaceIdentityError(
                    "selected scenario_id does not match persisted workspace identity"
                )
            if scenario_version != identity.scenario_version:
                raise WorkspaceIdentityError(
                    "selected scenario_version does not match persisted workspace "
                    "identity"
                )
        else:
            if not args.legacy_diagnostic:
                raise WorkspaceIdentityError(
                    "workspace identity is missing; use --legacy-diagnostic with "
                    "explicit --scenario-id and --scenario-version for "
                    "diagnostic-only evaluation"
                )
            if not args.scenario_id or not args.scenario_version:
                raise WorkspaceIdentityError(
                    "legacy diagnostic evaluation requires --scenario-id and "
                    "--scenario-version"
                )
            scenario_id = args.scenario_id
            scenario_version = args.scenario_version
        rules = rules_for_scenario(scenario_id, scenario_version)
        evaluation = evaluate_workspace(
            args.workspace,
            rules,
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
