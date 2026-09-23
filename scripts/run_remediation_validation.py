#!/usr/bin/env python3
"""Run the offline remediation gate using the repository's production fixtures.

This command composes existing pytest fixtures and performs read-only inspection
of the retained v8 run. It does not import scenario truth into agent contexts,
execute retained SQL, call a model, or modify production analytical behavior.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / ".runs" / "remediation-validation"
JSON_OUTPUT = OUTPUT_DIR / "remediation-validation.json"
MARKDOWN_OUTPUT = OUTPUT_DIR / "remediation-validation.md"
V8_DIR = ROOT / ".runs" / "phase2-task10-20260820-v8"
V8_MANIFEST = V8_DIR / "benchmark.json"
V8_RESCORING = V8_DIR / "benchmark-rescored.json"
DOCKER_IMAGE = "data-science-agent-python:latest"
EXPECTED_SCENARIO_COUNT = 10
EXPECTED_RETAINED_CELLS = 60


def _pytest_family(
    family_id: str,
    title: str,
    covers: list[str],
    paths: list[str],
    keyword: str | None = None,
) -> dict[str, Any]:
    command = ["uv", "run", "pytest", "-m", "not live", "-q", *paths]
    if keyword:
        command.extend(["-k", keyword])
    return {
        "id": family_id,
        "title": title,
        "covers": covers,
        "command": command,
    }


FAMILIES = [
    _pytest_family(
        "persisted_execution_evidence_and_inspection",
        "Persisted execution results and read-only evidence inspection",
        [
            "successful execution results retain canonical inspectable evidence",
            "inspection retrieves retained calculations without rerunning them",
            "truncated or failed results cannot establish complete evidence",
        ],
        [
            "tests/test_execution_evidence.py",
            "tests/test_sql.py",
            "tests/test_python.py",
            "tests/test_analytical_primitives.py::test_retained_record_is_inspectable_without_recomputation",
            "tests/test_analytical_agent_integration.py::test_large_model_response_keeps_ids_and_explicit_inspection_route",
        ],
    ),
    _pytest_family(
        "cohort_entity_and_join_grain",
        "Cohort population, entity grain, and maturity",
        [
            "repeat orders count each customer once",
            "zero-order entities remain in inclusive cohorts",
            "half-open boundary-day events obey the observation window",
            "incomplete cohorts are surfaced under the declared maturity policy",
            "duplicate dimension joins cannot inflate entity counts or sums",
        ],
        ["tests/test_analytical_primitives.py"],
        "entity_first_zero_activity or maturity_outcomes_are_explicit "
        "or duplicate_grain_from_dimension_join",
    ),
    _pytest_family(
        "metric_ratio_scope_and_components",
        "Ratio, component, and scope semantics",
        [
            "ratio-of-sums is distinct from mean-of-ratios",
            "zero denominators and null populations are explicit",
            "population, window, dimension, and result scopes must be compatible",
            "overall and segment scopes remain distinct",
            "component values and result identities remain explicit",
        ],
        [
            "tests/test_analytical_primitives.py",
            "tests/test_analytical_scope.py",
        ],
        "ratio_of_sums_and_mean_of_ratios or zero_denominator_and_null_handling "
        "or independent_totals_ratio_and_contrasts_check_scope "
        "or period_difference_relative_change_negative_and_zero_baseline "
        "or ratio_rejects_different_inclusion_populations "
        "or derived_contrasts or contrast_rejects_changed_observation_window "
        "or coverage_contrast_requires_same_expectation "
        "or reconciliation_contrast_requires_same_identity "
        "or interval_contrast_requires_same_confidence_level "
        "or ratio_keeps_selected_component_roles "
        "or component_and_indicator_ratio_building_blocks",
    ),
    _pytest_family(
        "coverage_grid_and_sparse_events",
        "Reporting grid, missing members, and sparse event coverage",
        [
            "an entire missing reporting date is found",
            "a missing dimension member on the latest date is found",
            "complete control data remains complete",
            "sparse event dates without a declared cadence are not incomplete",
        ],
        [
            "tests/test_analytical_primitives.py",
            "tests/test_analytical_agent_integration.py",
            "tests/test_data_quality_statistics_scenarios.py",
        ],
        "expected_grid_counts_actual_observations "
        "or sparse_events_without_expectation_and_explicit_date_set "
        "or auditor_coverage_distinguishes_expected_gap_from_sparse_events "
        "or clean_ecommerce_baseline_has_no_reporting_coverage_defect",
    ),
    _pytest_family(
        "economic_reconciliation",
        "Floating residual versus material reconciliation discrepancy",
        [
            "harmless floating-point residual passes",
            "materially different reconciliation fails",
        ],
        ["tests/test_analytical_primitives.py"],
        "reconciliation_separates_float_noise_from_material_error",
    ),
    _pytest_family(
        "binary_experiment_statistics",
        "Binary experiment estimates and input validity",
        [
            "meaningful, null, and immaterial effects match independent values",
            "arm swaps reverse the estimate and unequal allocation is respected",
            "duplicate subjects and missing or invalid outcomes are rejected",
            "estimate, interval, p-value, effect size, and practical decision match",
        ],
        [
            "tests/test_analytical_primitives.py",
            "tests/test_analytical_agent_integration.py",
            "tests/test_data_quality_statistics_scenarios.py",
        ],
        "binary_procedure_independent_expected_values "
        "or binary_tool_values_survive_specialist_lead_critic_report_and_evaluator "
        "or generalist_uses_the_same_bound_finalizer_path "
        "or arm_swap_unequal_sizes_and_p02_exact_propagation "
        "or invalid_binary_data_fails_clearly "
        "or statistical_evaluator_rejects_incomplete_or_wrong_claims",
    ),
    _pytest_family(
        "numerical_binding_selection_and_propagation",
        "Computed values, selected identities, and downstream propagation",
        [
            "model-authored numerical substitutions are rejected or hydrated",
            "values stay exact through specialist, Lead, Critic, state, report",
            "and evaluator",
            "superseded statistics do not compete with the selected final value",
            "unresolved selected conflicts fail",
        ],
        [
            "tests/test_result_binding.py",
            "tests/test_analytical_agent_integration.py",
            "tests/test_p13_semantic_identity.py",
        ],
    ),
    _pytest_family(
        "critic_evidence_and_bounded_repair",
        "Critic evidence, blocking errors, honest limitations, and targeted repair",
        [
            "wrong grain and denominator remain blocking",
            "fabricated Critic evidence is rejected",
            "correct results with honest limitations can complete",
            "optional causal/external-validity follow-up does not block indefinitely",
            "targeted repair preserves unrelated correct work",
        ],
        [
            "tests/test_finalization_repair.py",
            "tests/test_critic.py",
            "tests/test_runner.py",
            "tests/test_generalist.py",
            "tests/test_task6_calibration.py::test_targeted_metric_defects_fail_numeric_scope_or_value_check",
        ],
    ),
    _pytest_family(
        "sql_cancellation_and_cleanup",
        "Native SQL interruption, terminal accounting, and worker cleanup",
        [
            "expensive SQL is interrupted",
            "one terminal event and one budget charge are persisted",
            "no orphan worker or late write remains",
            "a subsequent query succeeds",
        ],
        ["tests/test_sql_reliability.py"],
    ),
    _pytest_family(
        "task_evaluator_metadata_and_version_boundaries",
        "Task/evaluator boundaries, semantic identity, negation, and chart contract",
        [
            "hidden sentinels stay out of model-visible context and tool surfaces",
            "public and evaluator periods agree",
            "issue type and scope matching ignores arbitrary issue IDs",
            "negated cautions differ from affirmative prohibited claims",
            "chart requirements agree across public task, runtime, and evaluator",
            "scenario 1.0/evaluator 1.2 stays frozen",
            "scenario 1.1/evaluator 1.3 preserves numeric values and tolerances",
        ],
        [
            "tests/test_agent_runtime.py",
            "tests/test_scenario_catalog.py",
            "tests/test_p13_semantic_identity.py",
            "tests/test_evaluation_contracts.py",
            "tests/test_evaluation_engine.py",
            "tests/test_data_quality_statistics_scenarios.py",
            "tests/test_benchmark_runner.py",
        ],
    ),
]


# These representatives and labels are drawn from the accepted forensic audit.
# This command verifies the retained artifacts still exist and reads their
# ledgers/reports only; it never executes saved SQL or recalculates v8 scores
# under evaluator 1.3.
RETAINED_REPLAY_CASES = [
    {
        "scenario_id": "canonical-q2-profitability",
        "architecture": "single-agent",
        "repetition": 2,
        "classification": "correctly_computed_and_surfaced",
        "finding": (
            "CAC and customer comparisons were surfaced; other requested "
            "comparisons were incomplete."
        ),
        "evidence_files": [
            "working/queries/q1_q2_profitability_by_channel.sql",
        ],
    },
    {
        "scenario_id": "canonical-q2-profitability",
        "architecture": "single-agent",
        "repetition": 2,
        "classification": "incorrect_computation",
        "finding": "The retained LTV cohort omitted zero-order acquired customers.",
        "evidence_files": [
            "working/queries/q1_q2_profitability_by_channel.sql",
        ],
    },
    {
        "scenario_id": "channel-mix-confounding",
        "architecture": "multi-agent",
        "repetition": 1,
        "classification": "incorrect_computation",
        "finding": (
            "A floating residual was treated as a material anomaly and "
            "diverted the investigation."
        ),
        "evidence_files": [],
    },
    {
        "scenario_id": "cogs-q2-margin-deterioration",
        "architecture": "single-agent",
        "repetition": 1,
        "classification": "incorrect_computation",
        "finding": (
            "An overall COGS ratio was surfaced instead of the required "
            "Google segment ratio."
        ),
        "evidence_files": [],
    },
    {
        "scenario_id": "discount-refund-q2-deterioration",
        "architecture": "single-agent",
        "repetition": 2,
        "classification": "required_investigation_absent",
        "finding": (
            "The run investigated November–December instead of the required "
            "Q1/Q2 discount/refund comparison."
        ),
        "evidence_files": [],
    },
    {
        "scenario_id": "meaningful-ab-treatment-effect",
        "architecture": "multi-agent",
        "repetition": 3,
        "classification": "correctly_computed_but_lost_or_mutated_downstream",
        "finding": (
            "The retained calculation produced p≈1.95049e-13; the typed "
            "specialist assessment contained p=1.0."
        ),
        "evidence_files": ["working/experiment_treatment_effect.json"],
    },
    {
        "scenario_id": "missing-reporting-day",
        "architecture": "single-agent",
        "repetition": 2,
        "classification": "incorrect_computation",
        "finding": (
            "The expected-grid query counted generated LEFT JOIN keys and "
            "reported 365 complete dates despite the missing date."
        ),
        "evidence_files": [
            "working/queries/audit_marketing_calendar_v2.sql",
        ],
    },
    {
        "scenario_id": "no-effect-ab-experiment",
        "architecture": "multi-agent",
        "repetition": 1,
        "classification": "correctly_computed_but_lost_or_mutated_downstream",
        "finding": (
            "Retained calculations estimated zero arm difference, but the "
            "final statistical identity did not match the required estimand."
        ),
        "evidence_files": [],
    },
    {
        "scenario_id": "partial-latest-reporting-day",
        "architecture": "multi-agent",
        "repetition": 2,
        "classification": "correctly_computed_but_lost_or_mutated_downstream",
        "finding": (
            "The latest-day gap was found, but the final metric surfaced four "
            "covered channels instead of 0.8."
        ),
        "evidence_files": [
            "outputs/reporting_period_completeness.json",
        ],
    },
    {
        "scenario_id": "retention-q2-deterioration",
        "architecture": "multi-agent",
        "repetition": 1,
        "classification": "incorrect_computation",
        "finding": (
            "A repaired cohort query counted joined order rows as acquired "
            "customers, inflating denominators."
        ),
        "evidence_files": [
            "working/queries/email_fixed_maturity_metrics.sql",
        ],
    },
    {
        "scenario_id": "significant-but-immaterial-ab-effect",
        "architecture": "multi-agent",
        "repetition": 3,
        "classification": "correctly_computed_but_lost_or_mutated_downstream",
        "finding": (
            "The retained code computed a small p-value, but the typed "
            "selected assessment copied p=1.0."
        ),
        "evidence_files": [],
    },
]

REPLAY_CLASSIFICATIONS = {
    "correctly_computed_and_surfaced",
    "correctly_computed_but_lost_or_mutated_downstream",
    "incorrect_computation",
    "required_investigation_absent",
    "no_usable_retained_evidence",
}

PYTEST_SUMMARY_STATUSES = (
    "passed",
    "failed",
    "errors",
    "skipped",
    "xfailed",
    "xpassed",
    "deselected",
)


def _safe_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("UV_CACHE_DIR", "/private/tmp/data-science-agent-uv")
    # Make accidental provider-backed execution impossible in this gate.
    for key in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
    ):
        env.pop(key, None)
    return env


def _run(command: list[str], *, env: dict[str, str]) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.monotonic() - started
    return {
        "command": shlex.join(command),
        "exit_code": completed.returncode,
        "duration_seconds": round(elapsed, 3),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _summary_counts(output: str) -> dict[str, int]:
    summary_line = ""
    for line in reversed(output.splitlines()):
        if " in " in line and any(status in line for status in PYTEST_SUMMARY_STATUSES):
            summary_line = line
            break
    counts = {status: 0 for status in PYTEST_SUMMARY_STATUSES}
    for status in PYTEST_SUMMARY_STATUSES:
        match = re.search(rf"\b(\d+)\s+{status}\b", summary_line)
        if match:
            counts[status] = int(match.group(1))
    counts["summary_available"] = int(bool(summary_line))
    return counts


def _run_test_family(spec: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    result = _run(spec["command"], env=env)
    output = result.pop("stdout") + result.pop("stderr")
    counts = _summary_counts(output)
    passed = (
        result["exit_code"] == 0
        and counts["summary_available"] == 1
        and counts["passed"] > 0
        and counts["failed"] == 0
        and counts["errors"] == 0
        and counts["skipped"] == 0
        and counts["xpassed"] == 0
    )
    return {
        "id": spec["id"],
        "title": spec["title"],
        "covers": spec["covers"],
        "command": result["command"],
        "status": "PASS" if passed else "FAIL",
        "duration_seconds": result["duration_seconds"],
        "pytest": counts,
        "failure_output": None if passed else output[-12000:],
    }


def _docker_server_info(env: dict[str, str]) -> dict[str, Any]:
    result = _run(["docker", "info"], env=env)
    output = result["stdout"] + result["stderr"]
    summary: dict[str, Any] = {
        "status": "PASS" if result["exit_code"] == 0 else "FAIL",
        "exit_code": result["exit_code"],
        "duration_seconds": result["duration_seconds"],
    }
    for label, pattern in (
        ("server_version", r"Server Version:\s*(.+)"),
        ("architecture", r"Architecture:\s*(.+)"),
        ("operating_system", r"Operating System:\s*(.+)"),
    ):
        match = re.search(pattern, output)
        if match:
            summary[label] = match.group(1).strip()
    if result["exit_code"] != 0:
        summary["failure_output"] = output[-4000:]
    return summary


def _tree_fingerprint(root: Path) -> dict[str, Any] | None:
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            digest.update(f"L\0{relative}\0{path.readlink()}\n".encode())
            continue
        if not path.is_file():
            continue
        file_count += 1
        size = path.stat().st_size
        total_bytes += size
        digest.update(f"F\0{relative}\0{size}\0".encode())
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\n")
    return {
        "sha256": digest.hexdigest(),
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


def _replay_retained_cases() -> dict[str, Any]:
    if not V8_DIR.is_dir() or not V8_MANIFEST.is_file():
        return {
            "status": "not_available",
            "reason": "The retained v8 directory or benchmark manifest is unavailable.",
            "scenario_families": [],
            "items": [],
        }

    manifest = json.loads(V8_MANIFEST.read_text(encoding="utf-8"))
    scenario_ids = {
        reference["scenario_id"] for reference in manifest["scenario_references"]
    }
    records = {
        (record["scenario_id"], record["architecture"], record["repetition"]): record
        for record in manifest["run_records"]
    }
    items: list[dict[str, Any]] = []
    for case in RETAINED_REPLAY_CASES:
        key = (case["scenario_id"], case["architecture"], case["repetition"])
        record = records.get(key)
        evidence: list[str] = []
        missing: list[str] = []
        if record is None:
            missing.append("benchmark run record")
            workspace = None
        else:
            workspace = V8_DIR / record["workspace_path"]
            for relative in (
                "state/analysis_ledger.json",
                "outputs/report.md",
                *case["evidence_files"],
            ):
                candidate = workspace / relative
                if candidate.is_file():
                    evidence.append(candidate.relative_to(ROOT).as_posix())
                else:
                    missing.append(relative)

        classification = case["classification"]
        availability = "available"
        if missing:
            availability = "not_available"
            classification = "no_usable_retained_evidence"
        if classification not in REPLAY_CLASSIFICATIONS:
            raise ValueError(
                f"Unknown retained replay classification: {classification}"
            )

        ledger_summary: dict[str, Any] | None = None
        if (
            workspace is not None
            and (workspace / "state/analysis_ledger.json").is_file()
        ):
            ledger = json.loads(
                (workspace / "state/analysis_ledger.json").read_text(encoding="utf-8")
            )
            ledger_summary = {
                "schema_version": ledger.get("schema_version"),
                "status": ledger.get("status"),
                "metric_comparisons": len(ledger.get("metric_comparisons", [])),
                "statistical_assessments": len(
                    ledger.get("statistical_assessments", [])
                ),
                "tool_events": len(ledger.get("tool_events", [])),
                "specialist_results": len(ledger.get("specialist_results", [])),
                "final_report_present": bool(ledger.get("final_report")),
            }

        items.append(
            {
                "scenario_id": case["scenario_id"],
                "architecture": case["architecture"],
                "repetition": case["repetition"],
                "classification": classification,
                "availability": availability,
                "finding": case["finding"]
                if availability == "available"
                else f"not_available: missing retained evidence {missing}",
                "evidence_files": evidence,
                "ledger_summary": ledger_summary,
            }
        )

    represented = {
        item["scenario_id"] for item in items if item["availability"] == "available"
    }
    available = sum(item["availability"] == "available" for item in items)
    return {
        "status": "PASS"
        if len(scenario_ids) == EXPECTED_SCENARIO_COUNT
        and len(represented) == EXPECTED_SCENARIO_COUNT
        else "PARTIAL",
        "mode": "read_only_retained_artifact_inspection",
        "source_manifest_id": manifest.get("manifest_id"),
        "source_manifest_scenario_count": len(scenario_ids),
        "representative_scenario_count": len(represented),
        "representative_item_count": len(items),
        "available_item_count": available,
        "not_available_item_count": len(items) - available,
        "saved_sql_executed": False,
        "workspaces_modified": False,
        "historical_scores_rewritten": False,
        "items": items,
    }


def _legacy_result_signature(record: dict[str, Any]) -> str:
    evaluation = record.get("evaluator_result") or {}
    checks = evaluation.get("checks") or []
    signature = {
        "status": evaluation.get("status"),
        "failure_reasons": evaluation.get("failure_reasons"),
        "score_breakdown": evaluation.get("score_breakdown"),
        "checks": [
            {
                "check_id": check.get("check_id"),
                "status": check.get("status"),
                "message": check.get("message"),
            }
            for check in checks
        ],
    }
    return json.dumps(signature, sort_keys=True, separators=(",", ":"))


def _replay_frozen_evaluator(env: dict[str, str]) -> dict[str, Any]:
    if not V8_MANIFEST.is_file() or not V8_RESCORING.is_file():
        return {
            "status": "not_available",
            "reason": "Frozen v8 manifest or baseline rescore is unavailable.",
        }

    baseline = json.loads(V8_RESCORING.read_text(encoding="utf-8"))
    result = _run(
        ["uv", "run", "python", "scripts/evaluate_manifest.py", str(V8_MANIFEST)],
        env=env,
    )
    try:
        current = json.loads(result["stdout"])
    except json.JSONDecodeError as error:
        return {
            "status": "FAIL",
            "reason": f"Offline evaluator did not return JSON: {error}",
            "command": result["command"],
            "exit_code": result["exit_code"],
            "failure_output": (result["stdout"] + result["stderr"])[-12000:],
        }

    current_manifest = current.get("manifest", {})
    current_records = {
        record["run_id"]: record for record in current_manifest.get("run_records", [])
    }
    baseline_records = {
        record["run_id"]: record for record in baseline.get("run_records", [])
    }
    differences: list[str] = []
    if set(current_records) != set(baseline_records):
        differences.append("run_id_set")
    for run_id in sorted(set(current_records) & set(baseline_records)):
        if _legacy_result_signature(
            current_records[run_id]
        ) != _legacy_result_signature(baseline_records[run_id]):
            differences.append(run_id)
            if len(differences) >= 10:
                break

    statuses: dict[str, int] = {}
    for record in current_records.values():
        status = (record.get("evaluator_result") or {}).get("status", "missing")
        statuses[status] = statuses.get(status, 0) + 1

    scenario_count = len(
        {
            (item.get("scenario_id"), item.get("scenario_version"))
            for item in current_manifest.get("scenario_references", [])
        }
    )
    compatible = (
        not differences
        and len(current_records) == EXPECTED_RETAINED_CELLS
        and scenario_count == EXPECTED_SCENARIO_COUNT
        and current_manifest.get("manifest_id") == "phase2-task10-20260820-v8"
    )
    return {
        "status": "PASS" if compatible else "FAIL",
        "mode": "offline_rescore_with_frozen_scenario_and_evaluator_versions",
        "evaluator_versions": sorted(
            {record.get("evaluator_version") for record in current_records.values()}
        ),
        "scenario_family_count": scenario_count,
        "run_record_count": len(current_records),
        "status_counts": statuses,
        "baseline_score_parity": "exact" if not differences else "mismatch",
        "differing_records": differences,
        "rescore_exit_code": result["exit_code"],
        "rescore_exit_code_note": (
            "Exit 1 is expected when frozen v8 records contain evaluator failures; "
            "parity compares historical results and is not a new-system pass."
            if result["exit_code"] == 1
            else None
        ),
        "command": result["command"],
        "current_evaluator_1_3_used": False,
        "historical_scores_rewritten": False,
    }


def _git_value(env: dict[str, str], *args: str) -> str | None:
    result = _run(["git", *args], env=env)
    return result["stdout"].strip() if result["exit_code"] == 0 else None


def _write_outputs(report: dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUTPUT.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Remediation validation gate",
        "",
        f"**Overall: {report['overall_status']}**",
        "",
        f"Created: `{report['created_at']}`  ",
        f"Commit: `{report['repository']['commit'] or 'unknown'}`",
        "",
        "## Validation families",
        "",
        "| Family | Result | Checks | Tests |",
        "| --- | --- | --- | ---: |",
    ]
    for family in report["families"]:
        counts = family.get("pytest", {})
        tests = (
            counts.get("passed", 0) + counts.get("failed", 0) + counts.get("errors", 0)
        )
        checks = "; ".join(family.get("covers", []))
        lines.append(
            f"| {family['title']} | **{family['status']}** | {checks} | {tests} |"
        )
    full_suite = report["full_suite"]
    counts = full_suite.get("pytest", {})
    docker_suite = report["docker"]["integration_tests"]
    docker_counts = docker_suite.get("pytest", {})
    docker_version = report["docker"]["daemon"].get(
        "server_version", "server version unavailable"
    )
    lines.extend(
        [
            "",
            "## Docker and complete offline suite",
            "",
            f"- Docker daemon: **{report['docker']['daemon']['status']}** "
            f"({docker_version}).",
            f"- Sandbox image build: **{report['docker']['image_build']['status']}**.",
            f"- Docker integration fixtures: **{docker_suite['status']}** "
            f"({docker_counts.get('passed', 0)} passed, "
            f"{docker_counts.get('skipped', 0)} skipped).",
            f"- `uv run pytest -m 'not live' -q`: **{full_suite['status']}** "
            f"({counts.get('passed', 0)} passed, {counts.get('failed', 0)} failed, "
            f"{counts.get('skipped', 0)} skipped, "
            f"{counts.get('deselected', 0)} deselected).",
            f"- Ruff check: **{report['quality_checks']['ruff_check']['status']}**.",
            f"- Ruff format check: "
            f"**{report['quality_checks']['ruff_format']['status']}**.",
            f"- Diff check: **{report['quality_checks']['diff_check']['status']}**.",
            "",
            "## Retained v8 replay and compatibility",
            "",
        ]
    )
    replay = report["retained_replay"]
    lines.append(
        f"Read-only retained inspection: **{replay['status']}**; "
        f"{replay.get('representative_scenario_count', 0)}/10 scenario families, "
        f"{replay.get('available_item_count', 0)}/"
        f"{replay.get('representative_item_count', 0)} "
        "representative items available. Saved SQL executed: **no**."
    )
    lines.extend(
        [
            "",
            "| Scenario | Run | Classification | Retained observation |",
            "| --- | --- | --- | --- |",
        ]
    )
    for item in replay.get("items", []):
        lines.append(
            f"| `{item['scenario_id']}` | {item['architecture']} r{item['repetition']} "
            f"| `{item['classification']}` | {item['finding']} |"
        )
    frozen = report["frozen_v8_compatibility"]
    lines.extend(
        [
            "",
            f"Frozen evaluator 1.2 rescore parity: **{frozen['status']}** "
            f"({frozen.get('run_record_count', 0)}/60 run records; "
            f"{frozen.get('baseline_score_parity', 'not available')}).",
            "This is compatibility with the frozen v8 evaluator and historical scores. "
            "It is not a claim that v8 would pass under the remediated system.",
            "",
            "## Gate predicates",
            "",
            "| Predicate | Result |",
            "| --- | --- |",
        ]
    )
    for name, passed in report["gate_predicates"].items():
        lines.append(
            f"| {name.replace('_', ' ')} | **{'PASS' if passed else 'FAIL'}** |"
        )
    lines.extend(["", "## Blockers", ""])
    blockers = report["unresolved_blockers"]
    lines.append(
        "None." if not blockers else "\n".join(f"- {item}" for item in blockers)
    )
    lines.extend(
        [
            "",
            "No live/model/paid calls were made. The paid matrix was not run",
            "or planned.",
            "",
        ]
    )
    MARKDOWN_OUTPUT.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    env = _safe_environment()
    blockers: list[str] = []
    print(
        "Remediation validation: offline mode; provider credentials removed.",
        flush=True,
    )

    retained_before = _tree_fingerprint(V8_DIR)
    replay = _replay_retained_cases()
    frozen_compatibility = _replay_frozen_evaluator(env)
    if frozen_compatibility["status"] != "PASS":
        blockers.append(
            "Frozen v8 evaluator 1.2 rescore did not match retained baseline outcomes."
        )

    docker_daemon = _docker_server_info(env)
    if docker_daemon["status"] != "PASS":
        blockers.append("Docker daemon is unavailable; Docker integration cannot pass.")
        image_build = {"status": "not_run", "reason": "Docker daemon unavailable."}
        docker_tests = {
            "id": "docker_integrations",
            "title": "Docker-backed sandbox integrations",
            "status": "FAIL",
            "pytest": {"passed": 0, "skipped": 0, "failed": 0, "errors": 1},
            "failure_output": "Docker daemon unavailable.",
        }
    else:
        print(
            "Docker daemon available; building the repository sandbox image.",
            flush=True,
        )
        image_build_result = _run(["docker", "build", "-t", DOCKER_IMAGE, "."], env=env)
        image_build = {
            "status": "PASS" if image_build_result["exit_code"] == 0 else "FAIL",
            "command": image_build_result["command"],
            "exit_code": image_build_result["exit_code"],
            "duration_seconds": image_build_result["duration_seconds"],
        }
        if image_build["status"] != "PASS":
            blockers.append("Repository sandbox Docker image build failed.")
            image_build["failure_output"] = (
                image_build_result["stdout"] + image_build_result["stderr"]
            )[-12000:]
        docker_spec = {
            "id": "docker_integrations",
            "title": "Docker-backed sandbox integrations",
            "covers": [
                "real sandbox execution works",
                "inputs and docs remain read-only",
                "sandbox network access is blocked",
                "SQL/Python/artifact/ledger path succeeds in Docker",
            ],
            "command": [
                "uv",
                "run",
                "pytest",
                "-m",
                "not live",
                "-q",
                "-rs",
                "tests/test_phase0_integration.py",
                "tests/test_python_docker.py",
            ],
        }
        docker_tests = _run_test_family(docker_spec, env)
        if docker_tests["status"] != "PASS":
            blockers.append("Docker-backed integration tests failed or were skipped.")

    families: list[dict[str, Any]] = []
    for spec in FAMILIES:
        print(f"Running validation family: {spec['title']}.", flush=True)
        family = _run_test_family(spec, env)
        families.append(family)
        if family["status"] != "PASS":
            blockers.append(f"Deterministic validation family failed: {family['id']}.")

    print("Running complete non-live pytest suite.", flush=True)
    full_suite_result = _run(["uv", "run", "pytest", "-m", "not live", "-q"], env=env)
    full_suite_output = full_suite_result.pop("stdout") + full_suite_result.pop(
        "stderr"
    )
    full_suite_counts = _summary_counts(full_suite_output)
    full_suite_pass = (
        full_suite_result["exit_code"] == 0
        and full_suite_counts["summary_available"] == 1
        and full_suite_counts["passed"] > 0
        and full_suite_counts["failed"] == 0
        and full_suite_counts["errors"] == 0
        and full_suite_counts["xpassed"] == 0
    )
    full_suite = {
        "status": "PASS" if full_suite_pass else "FAIL",
        "command": full_suite_result["command"],
        "duration_seconds": full_suite_result["duration_seconds"],
        "pytest": full_suite_counts,
        "failure_output": None if full_suite_pass else full_suite_output[-12000:],
    }
    if not full_suite_pass:
        blockers.append("Complete non-live pytest suite failed.")
    if docker_tests.get("pytest", {}).get("skipped", 0) != 0:
        blockers.append("Docker integration fixtures were unexpectedly skipped.")

    print("Running Ruff and diff checks.", flush=True)
    quality_checks: dict[str, Any] = {}
    for check_id, command in (
        ("ruff_check", ["uv", "run", "ruff", "check", "."]),
        ("ruff_format", ["uv", "run", "ruff", "format", "--check", "."]),
        ("diff_check", ["git", "diff", "--check"]),
    ):
        result = _run(command, env=env)
        quality_checks[check_id] = {
            "status": "PASS" if result["exit_code"] == 0 else "FAIL",
            "command": result["command"],
            "exit_code": result["exit_code"],
            "duration_seconds": result["duration_seconds"],
            "output": (result["stdout"] + result["stderr"])[-4000:],
        }
        if result["exit_code"] != 0:
            blockers.append(f"Quality check failed: {check_id}.")

    retained_after = _tree_fingerprint(V8_DIR)
    retained_unchanged = (
        retained_before is not None and retained_before == retained_after
    )
    if retained_before is not None and not retained_unchanged:
        blockers.append("Frozen v8 retained artifacts changed during validation.")
    family_status = {family["id"]: family["status"] == "PASS" for family in families}
    if frozen_compatibility["status"] == "PASS":
        frozen_compatibility_gate = retained_unchanged
        frozen_compatibility_basis = "retained_v8_rescore_parity"
    elif frozen_compatibility["status"] == "not_available":
        frozen_compatibility_gate = family_status.get(
            "task_evaluator_metadata_and_version_boundaries", False
        )
        frozen_compatibility_basis = "independent_legacy_compatibility_fixtures"
    else:
        frozen_compatibility_gate = False
        frozen_compatibility_basis = "retained_v8_rescore_mismatch"
    gate_predicates = {
        "all_required_deterministic_families_pass": all(family_status.values()),
        "docker_integration_passes_without_skips": docker_tests["status"] == "PASS"
        and docker_tests.get("pytest", {}).get("skipped", 0) == 0,
        "no_computed_to_final_numerical_corruption": family_status.get(
            "numerical_binding_selection_and_propagation", False
        ),
        "no_material_false_clean_data_quality_result": family_status.get(
            "coverage_grid_and_sparse_events", False
        ),
        "no_benchmark_metadata_leakage": family_status.get(
            "task_evaluator_metadata_and_version_boundaries", False
        ),
        "no_unbounded_sql_worker_remains": family_status.get(
            "sql_cancellation_and_cleanup", False
        ),
        "frozen_v8_compatibility_remains_intact": frozen_compatibility_gate,
        "complete_non_live_test_suite_passes": full_suite["status"] == "PASS",
        "ruff_and_diff_checks_pass": all(
            result["status"] == "PASS" for result in quality_checks.values()
        ),
    }
    overall = "PASS" if all(gate_predicates.values()) and not blockers else "FAIL"
    report = {
        "schema_version": "1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "overall_status": overall,
        "repository": {
            "commit": _git_value(env, "rev-parse", "HEAD"),
            "expected_reviewed_baseline_commit": (
                "a6ebfd2828b2bc0d9877a5eda4a02d11230241e6"
            ),
            "working_tree_status": _git_value(env, "status", "--short"),
        },
        "execution_safety": {
            "live_model_or_paid_api_calls_made": False,
            "provider_credentials_removed_from_child_processes": True,
            "paid_screening_matrix_run": False,
            "production_analytical_behavior_changed": False,
            "retained_pathological_sql_executed": False,
            "frozen_v8_tree_unchanged": retained_unchanged,
            "frozen_v8_tree_before": retained_before,
            "frozen_v8_tree_after": retained_after,
        },
        "docker": {
            "daemon": docker_daemon,
            "image": DOCKER_IMAGE,
            "image_build": image_build,
            "integration_tests": docker_tests,
        },
        "families": families,
        "full_suite": full_suite,
        "quality_checks": quality_checks,
        "retained_replay": replay,
        "frozen_v8_compatibility": frozen_compatibility,
        "frozen_v8_compatibility_basis": frozen_compatibility_basis,
        "gate_predicates": gate_predicates,
        "unresolved_blockers": blockers,
        "paid_matrix": "not run or planned",
    }
    _write_outputs(report)
    print(f"Overall remediation validation: {overall}.", flush=True)
    print(f"JSON: {JSON_OUTPUT.relative_to(ROOT)}", flush=True)
    print(f"Markdown: {MARKDOWN_OUTPUT.relative_to(ROOT)}", flush=True)
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
