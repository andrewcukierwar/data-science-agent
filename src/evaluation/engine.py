"""Generic zero-API evaluation engine for persisted workspaces."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agents.evidence import executed_references
from evaluation.contracts import (
    BenchmarkManifest,
    BenchmarkRunRecord,
    EvaluationCheck,
    EvaluationCheckStatus,
    EvaluatorResult,
    EvaluatorStatus,
    LifecycleStatus,
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
    WorkspaceIdentityError,
    verify_identity_matches_rules,
    verify_workspace_identity,
    verify_workspace_identity_for_rules,
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
    """Evaluate one persisted workspace using only deterministic primitives.

    A persisted identity is always verified and must select the supplied
    scenario/version/evaluator rules. ``expected_identity`` additionally binds
    the complete benchmark manifest identity for rescore callers.
    """

    snapshot = load_workspace_snapshot(workspace)
    if expected_identity is not None:
        identity = verify_workspace_identity(snapshot.workspace, expected_identity)
    else:
        identity = None
        identity_path = workspace_identity_path(snapshot.workspace)
        if identity_path.is_symlink() or identity_path.exists():
            identity = verify_workspace_identity_for_rules(
                snapshot.workspace,
                scenario_id=rules.scenario_id,
                scenario_version=rules.scenario_version,
                evaluator_version=rules.evaluator_version,
            )
    if identity is not None:
        verify_identity_matches_rules(
            identity,
            scenario_id=rules.scenario_id,
            scenario_version=rules.scenario_version,
            evaluator_version=rules.evaluator_version,
        )
    state = snapshot.state
    # One resolution of executed evidence for the whole evaluation, so the
    # audit, capability, and provenance checks cannot disagree about what
    # counts as a successful execution or a verified artifact.
    executed_refs = executed_references(snapshot.ledger)
    checks: list[EvaluationCheck] = []
    checks.extend(evaluate_lifecycle(state))
    checks.extend(
        evaluate_data_quality(
            state,
            rules.data_quality_policy,
            executed_refs=executed_refs,
        )
    )
    checks.extend(
        evaluate_capabilities(
            snapshot.workspace,
            state,
            rules.capability_policy,
            executed_refs=executed_refs,
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


def _rules_for_manifest_record(
    record: BenchmarkRunRecord,
    rules_by_scenario: Mapping[str | tuple[str, str], ScenarioRules],
) -> ScenarioRules:
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
        rules.scenario_id != record.scenario_id
        or rules.scenario_version != record.scenario_version
        or record.evaluator_version != rules.evaluator_version
    ):
        raise ValueError(
            f"record {record.run_id} does not match the selected evaluator rules"
        )
    return rules


def _manifest_reference(
    manifest: BenchmarkManifest,
    record: BenchmarkRunRecord,
):
    try:
        return next(
            item
            for item in manifest.scenario_references
            if item.scenario_id == record.scenario_id
            and item.scenario_version == record.scenario_version
        )
    except StopIteration as exc:
        raise ValueError(
            f"record {record.run_id} has no matching manifest scenario reference"
        ) from exc


def _evaluator_exception_result(
    record: BenchmarkRunRecord,
    rules: ScenarioRules,
    error: Exception,
) -> EvaluatorResult:
    """Represent one evaluator crash without turning it into an analysis failure."""

    message = f"offline rescore failed: {type(error).__name__}: {error}"
    status = (
        EvaluatorStatus.ERROR
        if record.lifecycle.status is LifecycleStatus.COMPLETED
        else EvaluatorStatus.NOT_EVALUATED
    )
    check_id = (
        "offline:evaluator_error"
        if status is EvaluatorStatus.ERROR
        else "offline:not_evaluated"
    )
    return EvaluatorResult(
        result_id=f"{record.run_id}-{rules.evaluator_version}",
        run_id=record.run_id,
        scenario_id=rules.scenario_id,
        scenario_version=rules.scenario_version,
        evaluator_version=rules.evaluator_version,
        status=status,
        checks=(
            EvaluationCheck(
                check_id=check_id,
                status=EvaluationCheckStatus.FAIL,
                message=message,
            ),
        ),
        error_message=message,
        evaluated_at=record.latency.finished_at,
    )


def _not_evaluated_result(
    record: BenchmarkRunRecord,
    rules: ScenarioRules,
    message: str,
) -> EvaluatorResult:
    """Keep non-completed lifecycle outcomes out of analytical scoring."""

    return EvaluatorResult(
        result_id=f"{record.run_id}-{rules.evaluator_version}",
        run_id=record.run_id,
        scenario_id=rules.scenario_id,
        scenario_version=rules.scenario_version,
        evaluator_version=rules.evaluator_version,
        status=EvaluatorStatus.NOT_EVALUATED,
        checks=(
            EvaluationCheck(
                check_id="offline:not_evaluated",
                status=EvaluationCheckStatus.WARN,
                message=message,
            ),
        ),
        failure_reasons=(message,),
        evaluated_at=record.latency.finished_at,
    )


def rescore_manifest(
    manifest: BenchmarkManifest,
    rules_by_scenario: Mapping[str | tuple[str, str], ScenarioRules],
    *,
    workspace_base_dir: str | Path | None = None,
    evaluator: Callable[..., OfflineEvaluation] | None = None,
) -> tuple[BenchmarkManifest, tuple[OfflineEvaluation, ...]]:
    """Canonical offline-rescore path shared by APIs and both CLIs.

    Workspace identity is verified before each record is evaluated. Analytical
    evaluator exceptions are isolated to that record; operational lifecycle
    outcomes remain untouched, and aggregates/comparisons are rebuilt from the
    resulting raw records before the manifest is returned.
    """

    evaluations: list[OfflineEvaluation] = []
    updated_records: list[BenchmarkRunRecord] = []
    evaluate = evaluator or evaluate_workspace
    for record in manifest.run_records:
        rules = _rules_for_manifest_record(record, rules_by_scenario)
        workspace_path = Path(record.workspace_path)
        if workspace_base_dir is not None and not workspace_path.is_absolute():
            workspace_path = Path(workspace_base_dir) / workspace_path
        reference = _manifest_reference(manifest, record)
        if reference.evaluator_version != record.evaluator_version:
            raise ValueError(
                f"record {record.run_id} does not match its manifest evaluator version"
            )
        if reference.seed != record.seed:
            raise ValueError(
                f"record {record.run_id} does not match its manifest scenario seed"
            )
        expected_identity = WorkspaceIdentity(
            benchmark_manifest_id=manifest.manifest_id,
            run_id=record.run_id,
            scenario_id=record.scenario_id,
            scenario_version=record.scenario_version,
            evaluator_version=reference.evaluator_version,
            architecture=record.architecture,
            repetition=record.repetition,
            seed=reference.seed,
            source_files=reference.source_files,
            code_revision=record.code_revision,
        )
        try:
            # Identity refusal is a manifest-integrity error, never an
            # evaluator outcome that may be converted into not_evaluated.
            verify_workspace_identity(workspace_path, expected_identity)
            if record.lifecycle.status is not LifecycleStatus.COMPLETED:
                evaluator_result = _not_evaluated_result(
                    record,
                    rules,
                    record.lifecycle.failure_message
                    or f"run lifecycle status is {record.lifecycle.status.value}",
                )
                try:
                    snapshot = load_workspace_snapshot(workspace_path)
                except Exception:  # noqa: BLE001
                    snapshot = None
                if snapshot is not None:
                    evaluations.append(
                        OfflineEvaluation(
                            result=evaluator_result,
                            snapshot=snapshot,
                            checks=evaluator_result.checks,
                        )
                    )

            else:
                try:
                    evaluation = evaluate(
                        workspace_path,
                        rules,
                        expected_identity=expected_identity,
                    )
                except WorkspaceIdentityError:
                    raise
                except Exception as error:  # noqa: BLE001
                    evaluator_result = _evaluator_exception_result(record, rules, error)
                    try:
                        snapshot = load_workspace_snapshot(workspace_path)
                    except Exception:  # noqa: BLE001
                        snapshot = None
                    if snapshot is not None:
                        evaluations.append(
                            OfflineEvaluation(
                                result=evaluator_result,
                                snapshot=snapshot,
                                checks=evaluator_result.checks,
                            )
                        )
                else:
                    snapshot = getattr(evaluation, "snapshot", None)
                    if snapshot is None:
                        try:
                            snapshot = load_workspace_snapshot(workspace_path)
                        except Exception:  # noqa: BLE001
                            snapshot = None
                    if snapshot is not None and snapshot.state.run_id != record.run_id:
                        raise ValueError(
                            f"workspace run ID {snapshot.state.run_id} does not match "
                            f"record {record.run_id}"
                        )
                    evaluator_result = evaluation.result
                    if snapshot is not None:
                        evaluations.append(
                            evaluation
                            if isinstance(evaluation, OfflineEvaluation)
                            else OfflineEvaluation(
                                result=evaluator_result,
                                snapshot=snapshot,
                                checks=evaluator_result.checks,
                            )
                        )
        except WorkspaceIdentityError:
            raise
        values = record.model_dump(mode="json")
        values.update(
            {
                "evaluator_version": rules.evaluator_version,
                "evaluator_result": evaluator_result.model_dump(mode="json"),
                "score_breakdown": (
                    evaluator_result.score_breakdown.model_dump(mode="json")
                    if evaluator_result.score_breakdown is not None
                    else None
                ),
            }
        )
        updated_records.append(BenchmarkRunRecord.model_validate(values))

    updated_references = []
    for reference in manifest.scenario_references:
        selected_rules = rules_by_scenario.get(
            (reference.scenario_id, reference.scenario_version)
        ) or rules_by_scenario.get(reference.scenario_id)
        updated_references.append(
            reference.model_copy(
                update=(
                    {"evaluator_version": selected_rules.evaluator_version}
                    if selected_rules is not None
                    else {}
                )
            )
        )
    values = manifest.model_dump()
    values["scenario_references"] = [
        reference.model_dump(mode="json") for reference in updated_references
    ]
    values["run_records"] = [
        record.model_dump(mode="json") for record in updated_records
    ]
    updated = BenchmarkManifest.model_validate(values)
    # Import lazily to avoid the benchmark package importing its runner while
    # this evaluation module is still being initialized.
    from benchmark.aggregation import aggregate_manifest

    return aggregate_manifest(updated), tuple(evaluations)


def evaluate_manifest(
    manifest: BenchmarkManifest,
    rules_by_scenario: Mapping[str | tuple[str, str], ScenarioRules],
    *,
    workspace_base_dir: str | Path | None = None,
    evaluator: Callable[..., OfflineEvaluation] | None = None,
) -> tuple[BenchmarkManifest, tuple[OfflineEvaluation, ...]]:
    """Backward-compatible alias for the canonical manifest rescorer."""

    return rescore_manifest(
        manifest,
        rules_by_scenario,
        workspace_base_dir=workspace_base_dir,
        evaluator=evaluator,
    )


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
    "rescore_manifest",
    "update_run_record",
]
