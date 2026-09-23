"""Complete frozen v8 evaluator signatures stay stable offline."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.engine import load_manifest, rescore_manifest
from evaluation.rules import rules_for_scenario

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / ".runs/phase2-task10-20260820-v8/benchmark.json"
RESCORED_PATH = ROOT / ".runs/phase2-task10-20260820-v8/benchmark-rescored.json"


def _evaluator_signature(record: dict) -> str:  # noqa: ANN001
    evaluation = record.get("evaluator_result") or {}
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
            for check in evaluation.get("checks") or []
        ],
    }
    return json.dumps(signature, sort_keys=True, separators=(",", ":"))


def test_all_retained_v8_evaluator_signatures_match_exactly() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    baseline = json.loads(RESCORED_PATH.read_text(encoding="utf-8"))
    rules = {
        (reference.scenario_id, reference.scenario_version): rules_for_scenario(
            reference.scenario_id, reference.scenario_version
        )
        for reference in manifest.scenario_references
    }

    rescored, _ = rescore_manifest(
        manifest,
        rules,
        workspace_base_dir=MANIFEST_PATH.parent,
    )
    actual_records = {
        record.run_id: record.model_dump(mode="json") for record in rescored.run_records
    }
    retained_records = {record["run_id"]: record for record in baseline["run_records"]}

    assert len(actual_records) == 60
    assert len(retained_records) == 60
    assert set(actual_records) == set(retained_records)
    assert all(
        _evaluator_signature(actual_records[run_id])
        == _evaluator_signature(retained_records[run_id])
        for run_id in sorted(retained_records)
    )
