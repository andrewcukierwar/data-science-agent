#!/usr/bin/env python3
"""Plan, pilot, resume, and offline-rescore benchmark matrices.

This command intentionally does not load ``.env``.  Only the live execution
path checks the process environment for credentials, after the caller has
passed ``--allow-paid``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
for import_root in (REPOSITORY_ROOT / "src", REPOSITORY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from benchmark import (  # noqa: E402
    BenchmarkError,
    BenchmarkRunner,
    build_benchmark_report,
)
from evaluation.contracts import ExecutionMode  # noqa: E402
from evaluation.engine import dump_stable_json, load_manifest  # noqa: E402


def _common_matrix_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--scenario-id",
        action="append",
        dest="scenario_ids",
        help="Scenario ID to include; repeat for a subset (default: catalog).",
    )
    parser.add_argument(
        "--architecture",
        action="append",
        dest="architectures",
        choices=("multi-agent", "single-agent"),
        help="Architecture to include; repeat for a subset (default: both).",
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--model",
        required=True,
        help="Exact model identifier to freeze into the benchmark manifest.",
    )
    parser.add_argument("--model-provider", default="openai")
    parser.add_argument(
        "--execution-mode",
        choices=tuple(mode.value for mode in ExecutionMode),
        default=ExecutionMode.LIVE.value,
    )
    parser.add_argument(
        "--repetition-justification",
        help="Required documentation when declaring fewer than three repetitions.",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Persist a new immutable matrix plan.")
    plan.add_argument("manifest", type=Path)
    plan.add_argument("--manifest-id")
    plan.add_argument("--workspace-base", type=Path, default=Path("workspaces"))
    _common_matrix_arguments(plan)

    dry_run = subparsers.add_parser(
        "dry-run", help="Print a matrix plan without writing or executing it."
    )
    dry_run.add_argument("--manifest-id", default="dry-run-manifest")
    dry_run.add_argument("--workspace-base", type=Path, default=Path("workspaces"))
    _common_matrix_arguments(dry_run)

    pilot = subparsers.add_parser(
        "pilot", help="Execute one cell as a cost-estimation pilot."
    )
    pilot.add_argument("manifest", type=Path)
    pilot.add_argument("--workspace-base", type=Path, default=Path("workspaces"))
    pilot.add_argument("--pilot-output", type=Path)
    pilot.add_argument(
        "--allow-paid",
        action="store_true",
        help=(
            "Explicitly authorize live paid execution; credentials are still required."
        ),
    )

    run = subparsers.add_parser(
        "run", help="Resume all missing cells after the pilot has been persisted."
    )
    run.add_argument("manifest", type=Path)
    run.add_argument("--workspace-base", type=Path, default=Path("workspaces"))
    run.add_argument("--pilot", type=Path)
    run.add_argument(
        "--allow-paid",
        action="store_true",
        help=(
            "Explicitly authorize live paid execution; credentials are still required."
        ),
    )
    run.add_argument(
        "--unknown-cost",
        action="store_true",
        help=(
            "Acknowledge that the pilot could not estimate cost and permit "
            "continuation beyond the pilot."
        ),
    )

    offline = subparsers.add_parser(
        "offline-rescore",
        help="Rescore existing workspaces into a new manifest without agents or APIs.",
    )
    offline.add_argument("manifest", type=Path)
    offline.add_argument("--output", type=Path)
    offline.add_argument("--workspace-base", type=Path)

    report = subparsers.add_parser(
        "report",
        help="Write a deterministic README-ready report from a manifest.",
    )
    report.add_argument("manifest", type=Path)
    report.add_argument("--output", type=Path)

    return parser


def _build_runner(args: argparse.Namespace) -> BenchmarkRunner:
    return BenchmarkRunner(args.workspace_base)


def _build_manifest(args: argparse.Namespace):
    runner = _build_runner(args)
    return runner, runner.build_manifest(
        manifest_id=args.manifest_id,
        scenario_ids=args.scenario_ids,
        architectures=tuple(args.architectures or ("multi-agent", "single-agent")),
        repetitions=args.repetitions,
        model=args.model,
        model_provider=args.model_provider,
        execution_mode=ExecutionMode(args.execution_mode),
        repetition_justification=args.repetition_justification,
    )


def _print_summary(summary) -> None:
    print(
        dump_stable_json(
            {
                "manifest": summary.manifest.model_dump(mode="json"),
                "executed_run_ids": summary.executed_run_ids,
                "skipped_run_ids": summary.skipped_run_ids,
                "failed_run_ids": summary.failed_run_ids,
                "interrupted": summary.interrupted,
            }
        ),
        end="",
    )


def _write_exclusive(path: Path, text: str) -> None:
    path = path.expanduser().resolve()
    if path.exists():
        raise BenchmarkError(f"refusing to overwrite existing report: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command in {"plan", "dry-run"}:
            runner, manifest = _build_manifest(args)
            if args.command == "plan":
                runner.persist_plan(manifest, args.manifest)
            print(
                dump_stable_json(
                    {
                        "manifest": manifest.model_dump(mode="json"),
                        "cells": [
                            {
                                "key": cell.key,
                                "run_id": cell.run_id,
                                "workspace_path": str(cell.workspace_path),
                            }
                            for cell in runner.planned_cells(manifest)
                        ],
                    }
                ),
                end="",
            )
            return 0

        if args.command == "pilot":
            runner = _build_runner(args)
            summary, report = runner.run_pilot(
                args.manifest,
                allow_paid=args.allow_paid,
                pilot_path=args.pilot_output,
            )
            _print_summary(summary)
            print(dump_stable_json(report.model_dump(mode="json")), end="")
            return 0 if not summary.failed_run_ids else 1

        if args.command == "run":
            runner = _build_runner(args)
            summary = runner.execute(
                args.manifest,
                resume=True,
                allow_paid=args.allow_paid,
                require_pilot=True,
                pilot_path=args.pilot,
                unknown_cost=args.unknown_cost,
            )
            _print_summary(summary)
            return 0 if not summary.failed_run_ids else 1

        if args.command == "offline-rescore":
            manifest_path = args.manifest.expanduser().resolve()
            runner = BenchmarkRunner(args.workspace_base or manifest_path.parent)
            rescored = runner.rescore(manifest_path, output_path=args.output)
            print(dump_stable_json(rescored.model_dump(mode="json")), end="")
            return (
                0
                if all(
                    record.evaluator_result.status.value == "pass"
                    for record in rescored.run_records
                )
                else 1
            )

        if args.command == "report":
            manifest = load_manifest(args.manifest)
            report = build_benchmark_report(manifest)
            output = dump_stable_json(report.model_dump(mode="json"))
            if args.output is None:
                print(output, end="")
            else:
                _write_exclusive(args.output, output)
            return 0
    except (BenchmarkError, ValueError, OSError) as error:
        print(f"BENCHMARK ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
