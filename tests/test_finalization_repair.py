"""Regression fixtures for the P1.2 objective-bound repair contract."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agents.critic import (
    candidate_completeness_validation,
    deterministic_candidate_validation,
)
from agents.finalization import (
    ORIGINAL_OBJECTIVE_REQUIREMENT,
    ReviewContractError,
    blocker_identity,
    build_validation_catalog,
    metric_definition_change_targets,
    preserve_unrelated_candidate,
    validate_review,
)
from agents.generalist import (
    _as_critic_candidate,
    persist_generalist_validation,
)
from agents.runtime import (
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    PermissionDeniedError,
)
from orchestration.ledger import AnalysisLedger
from orchestration.runner import AnalysisRunner
from schemas.audit import AuditResult, AuditStatus
from schemas.findings import ConfidenceLevel, Finding
from schemas.generalist import GeneralistResult
from schemas.lead import LeadResult
from schemas.metrics import MetricComparison, MetricComparisonType
from schemas.run_state import ToolEvent, ToolEventStatus
from schemas.statistics import (
    CausalInterpretation,
    ConfidenceInterval,
    StatisticalAssessment,
    StatisticalConclusion,
)
from schemas.validation import (
    BlockerCategory,
    CriticCandidate,
    EvidenceAnchorSource,
    LimitationCategory,
    ObjectionEvidence,
    RepairClass,
    ValidationBlocker,
    ValidationIssue,
    ValidationLimitation,
    ValidationResult,
    ValidationSeverity,
    ValidationStatus,
)
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager


def _context(
    tmp_path: Path,
    *,
    objective: str = "Compare North and South revenue.",
) -> AgentRunContext:
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace("p12")
    ledger = AnalysisLedger(workspace, objective=objective)
    return AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(run_id="p12", agent_role=AgentRole.GENERALIST),
    )


def _enable_cogs(context: AgentRunContext, tmp_path: Path) -> None:
    path = tmp_path / "orders.csv"
    path.write_text("cogs", encoding="utf-8")
    context.sql_service._input_relations["orders"] = path


def _blocker(candidate: CriticCandidate, **updates: object) -> ValidationBlocker:
    values: dict[str, object] = {
        "category": BlockerCategory.OBJECTIVE_NOT_ANSWERED,
        "requirement_id": "requirement:evidence",
        "target_id": "target:answer",
        "evidence": [
            ObjectionEvidence(
                source=EvidenceAnchorSource.CANDIDATE,
                pointer="/answer",
                value=candidate.answer,
            )
        ],
        "message": "The denominator uses all accounts.",
        "smallest_feasible_repair": "Select the already-computed active-account rate.",
        "repair_class": RepairClass.SYNTHESIS_SELECTION,
    }
    values.update(updates)
    return ValidationBlocker.model_validate(values)


def test_real_event_id_cannot_launder_a_fabricated_value(tmp_path: Path) -> None:
    context = _context(tmp_path)
    now = datetime.now(UTC)
    context.ledger.append_tool_event(
        ToolEvent(
            id="tool-real",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=now,
            completed_at=now,
            output={"rows": [[17]]},
        )
    )
    candidate = CriticCandidate(
        objective="Compare North and South revenue.", answer="North was higher."
    )
    review = ValidationResult(
        status=ValidationStatus.REVISE,
        blockers=[
            _blocker(
                candidate,
                category=BlockerCategory.INCORRECT_NUMERICAL_CLAIM,
                evidence=[
                    ObjectionEvidence(
                        source=EvidenceAnchorSource.TOOL_EVENT,
                        event_id="tool-real",
                        pointer="/output/rows/0/0",
                        value=99,
                    )
                ],
            )
        ],
    )

    with pytest.raises(ReviewContractError, match="does not match retained"):
        validate_review(review, candidate, context.ledger)


def test_anchor_rejects_a_different_nested_retained_value(tmp_path: Path) -> None:
    from agents.finalization import _validate_anchor

    context = _context(tmp_path)
    now = datetime.now(UTC)
    context.ledger.append_tool_event(
        ToolEvent(
            id="tool-nested",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=now,
            completed_at=now,
            output={"rows": [{"value": [None, True, 17]}]},
        )
    )
    candidate = CriticCandidate(
        objective="Compare North and South revenue.", answer="North was higher."
    )
    matching = ObjectionEvidence(
        source=EvidenceAnchorSource.TOOL_EVENT,
        event_id="tool-nested",
        pointer="/output/rows/0",
        value={"value": [None, True, 17]},
    )
    _validate_anchor(matching, candidate, context.ledger)

    changed = ObjectionEvidence.model_validate(
        {**matching.model_dump(mode="json"), "value": {"value": [None, True, 18]}}
    )
    with pytest.raises(ReviewContractError, match="does not match retained"):
        _validate_anchor(changed, candidate, context.ledger)


def test_legacy_issue_cannot_launder_a_fabricated_evidence_reference(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    now = datetime.now(UTC)
    context.ledger.append_tool_event(
        ToolEvent(
            id="tool-real",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=now,
            completed_at=now,
            output={"rows": [[17]]},
        )
    )
    candidate = CriticCandidate(
        objective=context.ledger.state.objective,
        answer="North was higher.",
    )
    review = ValidationResult(
        status=ValidationStatus.REVISE,
        issues=[
            ValidationIssue(
                id="legacy-1",
                severity=ValidationSeverity.HIGH,
                category="denominator",
                message="The denominator is wrong.",
                evidence_refs=["tool-real", "tool-fabricated"],
            )
        ],
    )

    with pytest.raises(ReviewContractError, match="tool-fabricated"):
        validate_review(review, candidate, context.ledger)


def test_typed_blocker_cannot_hide_unvalidated_legacy_issue(tmp_path: Path) -> None:
    context = _context(tmp_path)
    candidate = CriticCandidate(
        objective=context.ledger.state.objective,
        answer="North was higher.",
    )
    review = ValidationResult(
        status="revise",
        blockers=[_blocker(candidate)],
        issues=[
            ValidationIssue(
                id="legacy-hidden",
                severity=ValidationSeverity.MEDIUM,
                message="A second untyped objection.",
                evidence_refs=["fabricated-event"],
            )
        ],
    )

    with pytest.raises(ReviewContractError, match="cannot mix"):
        validate_review(review, candidate, context.ledger)


def test_validated_legacy_definition_issue_targets_only_its_metric(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    now = datetime.now(UTC)
    context.ledger.append_tool_event(
        ToolEvent(
            id="tool-metric",
            tool_name="run_sql",
            status=ToolEventStatus.SUCCEEDED,
            started_at=now,
            completed_at=now,
            output={"rows": [[0.2]]},
        )
    )
    comparison = MetricComparison(
        result_id="result-metric",
        metric_key="cac",
        baseline_period="Q1",
        comparison_period="Q2",
        comparison_type=MetricComparisonType.RELATIVE_CHANGE,
        value=0.2,
        unit="relative_change_fraction",
        evidence_refs=["tool-metric"],
    )
    candidate = CriticCandidate(
        objective=context.ledger.state.objective,
        answer="CAC increased.",
        metric_comparisons=[comparison],
    )
    review = ValidationResult(
        status="revise",
        issues=[
            ValidationIssue(
                id="legacy-definition",
                severity=ValidationSeverity.HIGH,
                category="metric_definition",
                message="The denominator is at the wrong grain.",
                evidence_refs=["tool-metric"],
            )
        ],
    )

    validated = validate_review(review, candidate, context.ledger)

    target = next(
        item.id
        for item in build_validation_catalog(candidate).targets
        if item.kind == "metric"
    )
    assert validated.blockers[0].target_id == target
    assert validated.blockers[0].affected_result_ids == ["result-metric"]
    assert metric_definition_change_targets(validated) == frozenset({target})


def test_fabricated_candidate_pointer_is_rejected(tmp_path: Path) -> None:
    context = _context(tmp_path)
    candidate = CriticCandidate(
        objective=context.ledger.state.objective,
        answer="North was higher.",
    )
    blocker = _blocker(
        candidate,
        evidence=[
            ObjectionEvidence(
                source=EvidenceAnchorSource.CANDIDATE,
                pointer="/findings/99/value",
                value=17,
            )
        ],
    )

    with pytest.raises(ReviewContractError, match="does not resolve"):
        validate_review(
            ValidationResult(status="revise", blockers=[blocker]),
            candidate,
            context.ledger,
        )


def test_blocker_identity_survives_rephrasing_reordering_and_value_change(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    first = CriticCandidate(
        objective="Compare North and South revenue.", answer="North was 21."
    )
    second = first.model_copy(update={"answer": "North was 19."})
    a = validate_review(
        ValidationResult(status="revise", blockers=[_blocker(first)]),
        first,
        context.ledger,
    )
    b = validate_review(
        ValidationResult(
            status="revise",
            blockers=[
                _blocker(
                    second,
                    message="All accounts were used as the denominator.",
                    smallest_feasible_repair="Use the active-account result.",
                    evidence=[
                        ObjectionEvidence(
                            source="candidate", pointer="/answer", value=second.answer
                        )
                    ],
                )
            ],
        ),
        second,
        context.ledger,
    )

    assert a.blockers[0].id == b.blockers[0].id


def test_deterministic_margin_and_visualization_blockers_have_distinct_identities(
    tmp_path: Path,
) -> None:
    context = _context(
        tmp_path, objective="Explain profit performance and create a chart."
    )
    _enable_cogs(context, tmp_path)
    candidate = CriticCandidate(
        objective=context.ledger.state.objective,
        answer="Profit changed.",
        visualization_requested=True,
    )

    validation = deterministic_candidate_validation(candidate, context)

    assert validation is not None
    assert validation.status is ValidationStatus.REVISE
    assert {
        (blocker.requirement_id, blocker.target_id) for blocker in validation.blockers
    } == {
        ("requirement:margin", "target:missing:requirement:margin"),
        (
            "requirement:visualization",
            "target:missing:requirement:visualization",
        ),
    }
    assert len({blocker.id for blocker in validation.blockers}) == 2
    assert context.ledger.budget.critic_loops == 0


def test_follow_up_acquisition_and_chart_requirements_can_coexist(
    tmp_path: Path,
) -> None:
    context = _context(
        tmp_path, objective="Analyze acquisition efficiency and create a chart."
    )
    context.sql_service._input_relations["sessions"] = tmp_path / "sessions.parquet"
    candidate = CriticCandidate(
        objective=context.ledger.state.objective,
        answer="Marketing spend increased while CAC was stable.",
        follow_up_analysis=True,
        follow_up_rationale="The channel-level question remains unresolved.",
    )

    validation = deterministic_candidate_validation(candidate, context)

    assert validation is not None
    assert {blocker.requirement_id for blocker in validation.blockers} == {
        "requirement:follow-up",
        "requirement:acquisition",
        "requirement:visualization",
    }
    assert len({blocker.id for blocker in validation.blockers}) == 3


def test_each_deterministic_completion_issue_uses_its_catalog_binding(
    tmp_path: Path,
) -> None:
    context = _context(
        tmp_path,
        objective=("Explain profit and acquisition efficiency, and create a chart."),
    )
    _enable_cogs(context, tmp_path)
    context.sql_service._input_relations["sessions"] = tmp_path / "sessions.parquet"
    candidate = CriticCandidate(
        objective=context.ledger.state.objective,
        answer="Profit declined. Marketing spend and CAC changed.",
        follow_up_analysis=True,
        follow_up_rationale="A material follow-up remains.",
        structured_metrics_required=True,
        visualization_requested=True,
    )
    completeness = candidate_completeness_validation(candidate, context=context)
    assert completeness is not None
    issues = [
        *completeness.issues,
        ValidationIssue(
            id="V-EVIDENCE-SOURCE-LINEAGE",
            severity=ValidationSeverity.HIGH,
            category="evidence_provenance",
            message="A material claim needs source-derived evidence.",
        ),
    ]

    validated = validate_review(
        ValidationResult(status=ValidationStatus.REVISE, issues=issues),
        candidate,
        context.ledger,
    )

    expected = {
        "requirement:follow-up",
        "requirement:margin",
        "requirement:acquisition",
        "requirement:structured-metrics",
        "requirement:visualization",
        "requirement:evidence",
    }
    assert {blocker.requirement_id for blocker in validated.blockers} == expected
    assert {blocker.target_id for blocker in validated.blockers} == {
        f"target:missing:{requirement}" for requirement in expected
    }
    assert len({blocker.id for blocker in validated.blockers}) == len(expected)


def test_equivalent_duplicate_blockers_deduplicate_but_conflicts_still_fail(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    candidate = CriticCandidate(
        objective=context.ledger.state.objective,
        answer="North was higher.",
    )
    blocker = _blocker(candidate)

    deduplicated = validate_review(
        ValidationResult(status="revise", blockers=[blocker, blocker.model_copy()]),
        candidate,
        context.ledger,
    )
    assert len(deduplicated.blockers) == 1
    assert deduplicated.blockers[0].id == blocker_identity(
        deduplicated.blockers[0], build_validation_catalog(candidate)
    )

    contradictory = _blocker(
        candidate,
        message="The same objective gap needs a different repair.",
        smallest_feasible_repair="Use another remediation.",
    )
    with pytest.raises(
        ReviewContractError, match="conflicting duplicate blocker identity"
    ):
        validate_review(
            ValidationResult(status="revise", blockers=[blocker, contradictory]),
            candidate,
            context.ledger,
        )


@pytest.mark.parametrize("category", list(LimitationCategory))
def test_limitation_categories_alone_do_not_block(
    tmp_path: Path, category: LimitationCategory
) -> None:
    context = _context(tmp_path)
    candidate = CriticCandidate(
        objective=context.ledger.state.objective, answer="Done."
    )
    result = validate_review(
        ValidationResult(
            status="pass",
            limitations=[ValidationLimitation(category=category, message="Disclosed.")],
        ),
        candidate,
        context.ledger,
    )

    assert result.status is ValidationStatus.PASS
    assert result.blockers == []


def test_missing_comparison_must_bind_to_exact_original_objective(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    candidate = CriticCandidate(
        objective=context.ledger.state.objective, answer="Done."
    )
    valid = _blocker(
        candidate,
        category=BlockerCategory.MISSING_REQUESTED_COMPARISON,
        requirement_id=ORIGINAL_OBJECTIVE_REQUIREMENT,
        target_id=f"target:missing:{ORIGINAL_OBJECTIVE_REQUIREMENT}",
        objective_clause="Compare North and South revenue",
    )
    result = validate_review(
        ValidationResult(status="revise", blockers=[valid]),
        candidate,
        context.ledger,
    )
    assert result.status is ValidationStatus.REVISE


def test_synthesis_repair_runtime_denies_computation(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.begin_finalization_repair(RepairClass.SYNTHESIS_SELECTION)
    try:
        for tool in ("run_sql", "run_python", "run_analytical"):
            with pytest.raises(PermissionDeniedError):
                context.require_permission(tool)
    finally:
        context.end_finalization_repair()


def test_catalog_deduplicates_ambiguous_targets(tmp_path: Path) -> None:
    context = _context(tmp_path)
    candidate = CriticCandidate(
        objective=context.ledger.state.objective, answer="Done."
    )
    catalog = build_validation_catalog(candidate)
    assert len({target.id for target in catalog.targets}) == len(catalog.targets)


def test_generalist_and_multi_agent_candidates_have_preflight_parity() -> None:
    objective = "Explain acquisition spend and CAC, and create a chart."
    lead = LeadResult(
        objective=objective,
        answer="Acquisition spend and CAC changed.",
        findings=[
            Finding(
                id="F1",
                statement="CAC increased.",
                metric="cac",
                value=0.2,
                value_unit="relative_change_fraction",
                evidence_refs=["tool-evidence"],
                confidence=ConfidenceLevel.MEDIUM,
            )
        ],
    )
    multi = AnalysisRunner._candidate(
        objective,
        lead,
        require_visualization=AnalysisRunner._objective_requests_visualization(
            objective
        ),
    )
    generalist = _as_critic_candidate(lead, objective=objective)

    assert generalist.structured_metrics_required is True
    assert generalist.visualization_requested is True
    assert generalist.model_dump() == multi.model_dump()
    assert build_validation_catalog(generalist) == build_validation_catalog(multi)
    generalist_failure = candidate_completeness_validation(generalist)
    multi_failure = candidate_completeness_validation(multi)
    assert generalist_failure is not None
    assert multi_failure is not None
    assert [issue.id for issue in generalist_failure.issues] == [
        issue.id for issue in multi_failure.issues
    ]


def test_generalist_and_multi_agent_share_deterministic_blocker_mapping(
    tmp_path: Path,
) -> None:
    objective = "Analyze acquisition efficiency and include a chart."
    context = _context(tmp_path, objective=objective)
    context.sql_service._input_relations["sessions"] = tmp_path / "sessions.parquet"
    lead = LeadResult(
        objective=objective,
        answer="Marketing spend increased while CAC was stable.",
    )
    multi = AnalysisRunner._candidate(
        objective,
        lead,
        require_visualization=AnalysisRunner._objective_requests_visualization(
            objective
        ),
    )

    multi_validation = deterministic_candidate_validation(multi, context)
    generalist_result = persist_generalist_validation(
        GeneralistResult(
            audit=AuditResult(status=AuditStatus.COMPLETE),
            candidate=lead,
            validation=ValidationResult(status=ValidationStatus.PASS),
        ),
        context,
    )

    assert multi_validation is not None
    assert [
        (item.requirement_id, item.target_id, item.id)
        for item in multi_validation.blockers
    ] == [
        (item.requirement_id, item.target_id, item.id)
        for item in generalist_result.validation.blockers
    ]


def test_targeted_repair_preserves_unrelated_findings_metrics_and_caveats() -> None:
    metric_one = MetricComparison(
        result_id="result-m1",
        metric_key="cac",
        baseline_period="Q1",
        comparison_period="Q2",
        comparison_type=MetricComparisonType.RELATIVE_CHANGE,
        value=0.2,
        unit="relative_change_fraction",
        evidence_refs=["tool-one"],
    )
    metric_two = metric_one.model_copy(
        update={
            "result_id": "result-m2",
            "metric_key": "ltv",
            "value": 0.1,
            "evidence_refs": ["tool-two"],
        }
    )
    finding_one = Finding(
        result_id="result-f1",
        id="F1",
        statement="CAC increased.",
        evidence_refs=["tool-one"],
        confidence=ConfidenceLevel.MEDIUM,
    )
    finding_two = finding_one.model_copy(
        update={
            "result_id": "result-f2",
            "id": "F2",
            "statement": "LTV was stable.",
            "evidence_refs": ["tool-two"],
        }
    )
    statistic_one = StatisticalAssessment(
        result_id="result-s1",
        metric_key="conversion_rate",
        baseline_period="Q1",
        comparison_period="Q2",
        method="two-proportion z test",
        unit_of_analysis="account",
        conclusion=StatisticalConclusion.NOT_STATISTICALLY_SIGNIFICANT,
        confidence_level=0.95,
        estimate=0.01,
        confidence_interval=ConfidenceInterval(lower=-0.02, upper=0.04),
        p_value=0.4,
        effect_size=0.01,
        practical_significance_threshold=0.05,
        practically_significant=False,
        assumptions_checked=("independent accounts",),
        causal_interpretation=CausalInterpretation.ASSOCIATION_ONLY,
        evidence_refs=["tool-one"],
    )
    statistic_two = statistic_one.model_copy(
        update={
            "result_id": "result-s2",
            "metric_key": "retention_rate",
            "evidence_refs": ["tool-two"],
        }
    )
    previous = LeadResult(
        selected_result_ids=[
            "result-f1",
            "result-f2",
            "result-m1",
            "result-m2",
            "result-s1",
            "result-s2",
        ],
        objective="Explain profitability.",
        answer="Initial answer.",
        findings=[finding_one, finding_two],
        metric_comparisons=[metric_one, metric_two],
        statistical_assessments=[statistic_one, statistic_two],
        caveats=["Observed data are non-experimental."],
    )
    corrected_finding = finding_one.model_copy(
        update={"statement": "CAC increased using acquired customers."}
    )
    corrected_metric = metric_one.model_copy(
        update={"result_id": "result-m1-corrected", "value": 0.15}
    )
    proposed = LeadResult(
        objective=previous.objective,
        answer="Corrected answer.",
        findings=[corrected_finding],
        metric_comparisons=[corrected_metric],
    )
    candidate = AnalysisRunner._candidate(previous.objective, previous)
    metric_target = next(
        target.id
        for target in build_validation_catalog(candidate).targets
        if target.kind == "metric" and "result-m1" in target.result_ids
    )
    review = ValidationResult(
        status="revise",
        blockers=[
            _blocker(
                candidate,
                category=BlockerCategory.WRONG_DENOMINATOR,
                target_id=metric_target,
                affected_result_ids=["result-m1"],
                repair_class=RepairClass.COMPUTATION,
            )
        ],
    )

    repaired = preserve_unrelated_candidate(previous, proposed, review)

    assert finding_one in repaired.findings
    assert corrected_finding not in repaired.findings
    assert finding_two in repaired.findings
    assert metric_two in repaired.metric_comparisons
    assert corrected_metric in repaired.metric_comparisons
    assert repaired.statistical_assessments == [statistic_one, statistic_two]
    assert "result-f2" in repaired.selected_result_ids
    assert "result-m2" in repaired.selected_result_ids
    assert "result-s2" in repaired.selected_result_ids
    assert repaired.caveats == previous.caveats
