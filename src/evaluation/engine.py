"""Generic zero-API evaluation engine for persisted workspaces."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from evaluation.contracts import (
    BenchmarkManifest,
    BenchmarkRunRecord,
    EvaluationCheck,
    EvaluationCheckStatus,
    EvaluatorResult,
    EvaluatorStatus,
    ScoreBreakdown,
    WorkspaceIdentity,
    check_workspace_version_compatibility,
)
from evaluation.primitives import (
    CapabilityPolicy,
    DataQualityPolicy,
    StatisticsPolicy,
    TaskCompletenessPolicy,
    TextRule,
    compile_final_metric_set,
    evaluate_capabilities,
    evaluate_data_quality,
    evaluate_lifecycle,
    evaluate_numeric_comparisons,
    evaluate_provenance,
    evaluate_root_cause,
    evaluate_statistics,
    evaluate_task_completeness,
    evaluate_unsupported_claims,
)
from evaluation.workspace_identity import (
    verify_workspace_identity,
    verify_workspace_identity_integrity,
    workspace_identity_path,
)
from orchestration.ledger import AnalysisLedger
from scenarios.definitions.models import GroundTruthMetric
from schemas.run_state import AnalysisRunState
from tools.workspace import Workspace, WorkspaceManager


@dataclass(frozen=True, slots=True)
class ScenarioRules:
    """Scenario-specific expectations composed from generic primitives."""

    scenario_id: str
    scenario_version: str
    evaluator_version: str
    expected_metrics: tuple[GroundTruthMetric, ...] = ()
    root_cause_rules: tuple[TextRule, ...] = ()
    data_quality_policy: DataQualityPolicy = field(default_factory=DataQualityPolicy)
    capability_policy: CapabilityPolicy = field(default_factory=CapabilityPolicy)
    statistics_policy: StatisticsPolicy = field(default_factory=StatisticsPolicy)
    task_policy: TaskCompletenessPolicy = field(default_factory=TaskCompletenessPolicy)
    unsupported_claim_patterns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    """Read-only typed view of one persisted workspace."""

    workspace: Workspace
    ledger: AnalysisLedger
    state: AnalysisRunState
    report_text: str


@dataclass(frozen=True, slots=True)
class OfflineEvaluation:
    """Stable evaluator result plus the snapshot used to produce it."""

    result: EvaluatorResult
    snapshot: WorkspaceSnapshot
    checks: tuple[EvaluationCheck, ...]

    @property
    def passed(self) -> bool:
        """Whether every hard evaluator check passed."""

        return self.result.status is EvaluatorStatus.PASS

    def as_dict(self) -> dict[str, object]:
        """Return a stable JSON-ready representation for CLI consumers."""

        return {
            "evaluator_result": self.result.model_dump(mode="json"),
            "workspace": str(self.snapshot.workspace.root),
        }


def _load_workspace(workspace: Workspace | str | Path) -> Workspace:
    if isinstance(workspace, Workspace):
        check_workspace_version_compatibility(workspace.root)
        return workspace
    root = Path(workspace).expanduser().resolve()
    check_workspace_version_compatibility(root)
    return WorkspaceManager(root.parent).open_workspace(root.name)


def load_workspace_snapshot(workspace: Workspace | str | Path) -> WorkspaceSnapshot:
    """Load persisted state without writing, importing agents, or using APIs."""

    opened = _load_workspace(workspace)
    ledger = AnalysisLedger(opened)
    state = ledger.state
    report_text = ""
    if state.final_report is not None:
        report_path = opened.root / state.final_report.path
        if report_path.is_file():
            report_text = report_path.read_text(encoding="utf-8")
    return WorkspaceSnapshot(
        workspace=opened,
        ledger=ledger,
        state=state,
        report_text=report_text,
    )


def _analysis_text(snapshot: WorkspaceSnapshot) -> str:
    return " ".join(
        [
            snapshot.report_text,
            *[finding.statement for finding in snapshot.state.findings],
        ]
    ).lower()


def _score_checks(checks: Sequence[EvaluationCheck]) -> ScoreBreakdown:
    """Turn deterministic checks into stable per-category scores."""

    grouped: dict[str, list[float]] = {}
    for check in checks:
        category = check.check_id.split(":", 1)[0]
        value = {
            EvaluationCheckStatus.PASS: 1.0,
            EvaluationCheckStatus.WARN: 0.5,
            EvaluationCheckStatus.FAIL: 0.0,
        }[check.status]
        grouped.setdefault(category, []).append(value)
    dimensions = {
        category: round(sum(values) / len(values), 12)
        for category, values in sorted(grouped.items())
    }
    overall = round(sum(dimensions.values()) / len(dimensions), 12)
    return ScoreBreakdown(dimensions=dimensions, overall_score=overall)


def evaluate_workspace(
    workspace: Workspace | str | Path,
    rules: ScenarioRules,
    *,
    expected_identity: WorkspaceIdentity | None = None,
) -> OfflineEvaluation:
    """Evaluate one persisted workspace using only deterministic primitives."""

    snapshot = load_workspace_snapshot(workspace)
    if expected_identity is not None:
        verify_workspace_identity(snapshot.workspace, expected_identity)
    elif workspace_identity_path(snapshot.workspace).is_file():
        verify_workspace_identity_integrity(snapshot.workspace)
    state = snapshot.state
    checks: list[EvaluationCheck] = []
    checks.extend(evaluate_lifecycle(state))
    checks.extend(evaluate_data_quality(state, rules.data_quality_policy))
    checks.extend(
        evaluate_capabilities(
            snapshot.workspace,
            state,
            rules.capability_policy,
        )
    )
    final_metrics, metric_set_checks = compile_final_metric_set(
        state.metric_comparisons
    )
    checks.extend(metric_set_checks)
    if rules.expected_metrics:
        checks.extend(
            evaluate_numeric_comparisons(
                final_metrics,
                rules.expected_metrics,
            )
        )
    else:
        checks.append(
            EvaluationCheck(
                check_id="numeric:not_required",
                status=EvaluationCheckStatus.PASS,
                message="no scenario-specific numeric ground truth was declared",
            )
        )
    checks.extend(evaluate_provenance(snapshot.workspace, state, snapshot.report_text))
    checks.extend(evaluate_root_cause(_analysis_text(snapshot), rules.root_cause_rules))
    checks.extend(
        evaluate_statistics(
            state,
            snapshot.report_text,
            rules.statistics_policy,
        )
    )
    checks.extend(
        evaluate_unsupported_claims(
            _analysis_text(snapshot),
            forbidden_patterns=rules.unsupported_claim_patterns
            or (
                r"\bproves?\b",
                r"\bguarantee[sd]?\b",
                r"\bdefinitively\b",
                r"\bcausal proof\b",
                r"\bwithout uncertainty\b",
            ),
        )
    )
    checks.extend(
        evaluate_task_completeness(
            snapshot.workspace,
            state,
            snapshot.report_text,
            rules.task_policy,
        )
    )

    score = _score_checks(checks)
    failures = tuple(
        check.message for check in checks if check.status is EvaluationCheckStatus.FAIL
    )
    status = EvaluatorStatus.FAIL if failures else EvaluatorStatus.PASS
    result = EvaluatorResult(
        result_id=f"{state.run_id}-{rules.evaluator_version}",
        run_id=state.run_id,
        scenario_id=rules.scenario_id,
        scenario_version=rules.scenario_version,
        evaluator_version=rules.evaluator_version,
        status=status,
        checks=tuple(checks),
        score_breakdown=score,
        failure_reasons=failures,
        evaluated_at=state.updated_at,
    )
    return OfflineEvaluation(result=result, snapshot=snapshot, checks=tuple(checks))


def update_run_record(
    record: BenchmarkRunRecord,
    evaluation: OfflineEvaluation,
) -> BenchmarkRunRecord:
    """Replace only the evaluator result and score in a raw benchmark record."""

    if record.workspace_path != str(evaluation.snapshot.workspace.root):
        # The path can be recorded relative to the caller's working directory;
        # identity is still protected by requiring the basename/run ID match.
        if Path(record.workspace_path).name != evaluation.snapshot.workspace.root.name:
            raise ValueError("benchmark record workspace does not match evaluation")
    values = record.model_dump()
    values.update(
        {
            "evaluator_result": evaluation.result.model_dump(),
            "score_breakdown": evaluation.result.score_breakdown.model_dump()
            if evaluation.result.score_breakdown is not None
            else None,
        }
    )
    return BenchmarkRunRecord.model_validate(values)


def evaluate_manifest(
    manifest: BenchmarkManifest,
    rules_by_scenario: Mapping[str | tuple[str, str], ScenarioRules],
    *,
    workspace_base_dir: str | Path | None = None,
) -> tuple[BenchmarkManifest, tuple[OfflineEvaluation, ...]]:
    """Offline-rescore every persisted run record in a manifest."""

    evaluations: list[OfflineEvaluation] = []
    updated_records: list[BenchmarkRunRecord] = []
    for record in manifest.run_records:
        try:
            rules = (
                rules_by_scenario.get((record.scenario_id, record.scenario_version))
                or rules_by_scenario[record.scenario_id]
            )
        except KeyError as exc:
            raise ValueError(
                "no offline evaluator is registered for scenario "
                f"{record.scenario_id}@{record.scenario_version}"
            ) from exc
        if (
            record.scenario_version != rules.scenario_version
            or record.evaluator_version != rules.evaluator_version
        ):
            raise ValueError(
                f"record {record.run_id} does not match evaluator rule versions"
            )
        workspace_path = Path(record.workspace_path)
        if workspace_base_dir is not None and not workspace_path.is_absolute():
            workspace_path = Path(workspace_base_dir) / workspace_path
        reference = next(
            item
            for item in manifest.scenario_references
            if item.scenario_id == record.scenario_id
            and item.scenario_version == record.scenario_version
        )
        expected_identity = WorkspaceIdentity(
            benchmark_manifest_id=manifest.manifest_id,
            run_id=record.run_id,
            scenario_id=record.scenario_id,
            scenario_version=record.scenario_version,
            evaluator_version=record.evaluator_version,
            architecture=record.architecture,
            repetition=record.repetition,
            seed=record.seed,
            source_files=reference.source_files,
            code_revision=record.code_revision,
        )
        evaluation = evaluate_workspace(
            workspace_path,
            rules,
            expected_identity=expected_identity,
        )
        if evaluation.snapshot.state.run_id != record.run_id:
            raise ValueError(
                f"workspace run ID {evaluation.snapshot.state.run_id} does not match "
                f"record {record.run_id}"
            )
        evaluations.append(evaluation)
        updated_records.append(update_run_record(record, evaluation))

    values = manifest.model_dump()
    values["run_records"] = [record.model_dump() for record in updated_records]
    updated = BenchmarkManifest.model_validate(values)
    return updated, tuple(evaluations)


def load_manifest(path: str | Path) -> BenchmarkManifest:
    """Load one JSON manifest without environment or network side effects."""

    manifest_path = Path(path).expanduser().resolve()
    return BenchmarkManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )


def dump_stable_json(value: object) -> str:
    """Serialize CLI output deterministically for repeatable offline scoring."""

    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


__all__ = [
    "OfflineEvaluation",
    "ScenarioRules",
    "WorkspaceSnapshot",
    "dump_stable_json",
    "evaluate_manifest",
    "evaluate_workspace",
    "load_manifest",
    "load_workspace_snapshot",
    "update_run_record",
]
