"""Deterministic fixtures for Task 5 data-quality and experiment scenarios."""

from collections.abc import Callable

import pandas as pd
import pytest

from evaluation.primitives import (
    DataQualityPolicy,
    DataQualityRequirement,
    evaluate_data_quality,
    evaluate_statistics,
    numeric_ground_truth_failures,
)
from evaluation.rules import (
    immaterial_experiment_rules,
    meaningful_experiment_rules,
    missing_reporting_day_rules,
    no_effect_experiment_rules,
    partial_latest_day_rules,
)
from scenarios import get_scenario
from scenarios.experiment_scenarios import statistical_assessment_for_scenario
from scenarios.generator import SyntheticEcommerceConfig, SyntheticEcommerceGenerator
from schemas.audit import (
    AuditResult,
    AuditStatus,
    DataQualityIssue,
    DataQualityIssueScope,
    DataQualityIssueType,
    IssueSeverity,
    TableAudit,
)
from schemas.findings import SpecialistResult
from schemas.run_state import AnalysisRunState, SpecialistResultRecord
from schemas.statistics import (
    CausalInterpretation,
    ConfidenceInterval,
)


def _ecommerce_scale() -> SyntheticEcommerceConfig:
    return SyntheticEcommerceConfig(
        seed=42,
        num_customers=1_000,
        num_orders=4_000,
        num_sessions=8_000,
        num_products=4,
        period_days=365,
    )


@pytest.mark.parametrize(
    "scenario_id",
    ("missing-reporting-day", "partial-latest-reporting-day"),
)
def test_data_quality_traps_are_observable_and_invariants_pass(
    scenario_id: str,
) -> None:
    registration = get_scenario(scenario_id, "1.0")
    generated = registration.generate_validated(_ecommerce_scale())

    assert registration.invariant_suite.validate(generated.dataset).passed
    assert (
        numeric_ground_truth_failures(
            registration.invariant_suite.metric_observer(generated.dataset),
            registration.evaluation_spec.ground_truth,
        )
        == []
    )

    spend = generated.dataset.marketing_spend
    dates = pd.to_datetime(spend["date"]).dt.date
    if scenario_id == "missing-reporting-day":
        assert dates.nunique() == 364
        assert len(spend) == 364 * 5
    else:
        assert dates.nunique() == 365
        latest = dates.max()
        assert spend.loc[dates.eq(latest), "channel"].nunique() == 4


def test_clean_ecommerce_baseline_has_no_reporting_coverage_defect() -> None:
    baseline = SyntheticEcommerceGenerator(_ecommerce_scale()).generate()
    spend = baseline.marketing_spend
    dates = pd.to_datetime(spend["date"]).dt.date

    assert dates.nunique() == 365
    assert spend.loc[dates.eq(dates.max()), "channel"].nunique() == 5


@pytest.mark.parametrize(
    ("expected_type", "expected_scope", "wrong_type", "rules"),
    (
        (
            DataQualityIssueType.MISSING_REPORTING_DAY,
            DataQualityIssueScope(
                relation="marketing_spend",
                date="2025-05-31",
                dimensions={},
            ),
            DataQualityIssueType.PARTIAL_REPORTING_DAY,
            missing_reporting_day_rules,
        ),
        (
            DataQualityIssueType.PARTIAL_REPORTING_DAY,
            DataQualityIssueScope(
                relation="marketing_spend",
                date="2025-12-31",
                dimensions={"channel": "Affiliate"},
            ),
            DataQualityIssueType.MISSING_REPORTING_DAY,
            partial_latest_day_rules,
        ),
    ),
)
def test_auditor_defect_recall_and_wrong_defect_rejection(
    expected_type: DataQualityIssueType,
    expected_scope: DataQualityIssueScope,
    wrong_type: DataQualityIssueType,
    rules: Callable,
) -> None:
    policy = rules().data_quality_policy

    correct_state = _state(
        audit=_audit(
            DataQualityIssue(
                id="DQ-001",
                issue_type=expected_type,
                scope=expected_scope,
                severity=IssueSeverity.MEDIUM,
                message="A classified issue with supported evidence.",
                table_name=expected_scope.relation,
                evidence_refs=["evidence:issue"],
            )
        )
    )
    correct_checks = evaluate_data_quality(
        correct_state,
        policy,
        executed_refs=_executed_refs(correct_state),
    )
    assert all(check.status.value == "pass" for check in correct_checks)

    wrong_state = _state(
        audit=_audit(
            DataQualityIssue(
                id="different-instance-id",
                issue_type=wrong_type,
                scope=expected_scope,
                severity=IssueSeverity.MEDIUM,
                message="Wrong classification.",
                table_name=expected_scope.relation,
                evidence_refs=["evidence:issue"],
            )
        )
    )
    wrong_checks = evaluate_data_quality(
        wrong_state,
        policy,
        executed_refs=_executed_refs(wrong_state),
    )
    assert any(check.status.value == "fail" for check in wrong_checks)


def test_data_quality_identity_ignores_id_but_requires_type_scope_and_proof() -> None:
    requirement = DataQualityRequirement(
        issue_type=DataQualityIssueType.OTHER,
        scope=DataQualityIssueScope(
            relation="orders_v2",
            date="2031-08-17",
            dimensions={"sales_region": "North-2"},
            value="refund_amount",
        ),
    )
    policy = DataQualityPolicy(
        maximum_issue_severity=IssueSeverity.HIGH,
        required_issues=(requirement,),
    )

    def check(issue: DataQualityIssue, *, evidence: set[str] | None = None):
        state = _state(audit=_audit(issue))
        return evaluate_data_quality(
            state,
            policy,
            executed_refs=_executed_refs(state) if evidence is None else evidence,
        )

    supported = DataQualityIssue(
        id="foo-7",
        issue_type=DataQualityIssueType.OTHER,
        scope=requirement.scope,
        severity=IssueSeverity.MEDIUM,
        message="Observed issue with changing business labels.",
        evidence_refs=["evidence:issue"],
    )
    supported_checks = check(supported)
    assert all(item.status.value == "pass" for item in supported_checks)

    renamed = supported.model_copy(update={"id": "instance-arbitrary-928"})
    assert [item.status for item in check(renamed)] == [
        item.status for item in supported_checks
    ]

    for wrong_scope in (
        requirement.scope.model_copy(update={"date": "2031-08-18"}),
        DataQualityIssueScope.model_validate(
            {
                **requirement.scope.model_dump(),
                "dimensions": {"sales_region": "South-9"},
            }
        ),
        requirement.scope.model_copy(update={"relation": "orders_v3"}),
    ):
        wrong = supported.model_copy(update={"scope": wrong_scope})
        assert any(item.status.value == "fail" for item in check(wrong))

    unsupported = supported.model_copy(update={"evidence_refs": ["unexecuted-ref"]})
    unsupported_checks = check(unsupported, evidence={"evidence:table-profile"})
    assert any(
        item.check_id == "data_quality:claim_provenance" and item.status.value == "fail"
        for item in unsupported_checks
    )


def test_clean_data_quality_policy_rejects_false_positive() -> None:
    policy = DataQualityPolicy(
        maximum_issue_severity=IssueSeverity.HIGH,
        forbid_any_issues=True,
    )
    clean_state = _state(audit=_clean_audit())
    clean_checks = evaluate_data_quality(
        clean_state,
        policy,
        executed_refs=_executed_refs(clean_state),
    )
    assert all(check.status.value == "pass" for check in clean_checks)

    false_positive_state = _state(
        audit=_audit(
            DataQualityIssue(
                id="false-positive-instance",
                issue_type=DataQualityIssueType.MISSING_REPORTING_DAY,
                severity=IssueSeverity.MEDIUM,
                message="Unsupported false positive.",
                evidence_refs=["evidence:issue"],
            )
        )
    )
    false_positive_checks = evaluate_data_quality(
        false_positive_state,
        policy,
        executed_refs=_executed_refs(false_positive_state),
    )
    assert any(
        check.check_id == "data_quality:no_issues" and check.status.value == "fail"
        for check in false_positive_checks
    )


@pytest.mark.parametrize(
    ("scenario_id", "expected_n", "control_rate", "treatment_rate"),
    (
        ("meaningful-ab-treatment-effect", 2_000, 0.20, 0.30),
        ("no-effect-ab-experiment", 2_000, 0.25, 0.25),
        ("significant-but-immaterial-ab-effect", 25_000, 0.30, 0.32),
    ),
)
def test_experiment_fixtures_have_stable_known_sampling_properties(
    scenario_id: str,
    expected_n: int,
    control_rate: float,
    treatment_rate: float,
) -> None:
    registration = get_scenario(scenario_id, "1.0")
    first = registration.generate_validated()
    second = registration.generate_validated()
    pd.testing.assert_frame_equal(
        first.dataset.observations,
        second.dataset.observations,
    )
    assert first.dataset.business_definitions == second.dataset.business_definitions

    observations = first.dataset.observations
    assert len(observations) == expected_n * 2
    assert observations.groupby("assignment")["outcome"].size().to_dict() == {
        "control": expected_n,
        "treatment": expected_n,
    }
    assert observations.groupby("assignment")["outcome"].mean().to_dict() == {
        "control": control_rate,
        "treatment": treatment_rate,
    }
    assessment = statistical_assessment_for_scenario(first.dataset, first.definition)
    assert (
        assessment.conclusion
        is first.definition.statistical_expectation.expected_conclusion
    )


@pytest.mark.parametrize(
    "scenario_id",
    (
        "meaningful-ab-treatment-effect",
        "no-effect-ab-experiment",
        "significant-but-immaterial-ab-effect",
    ),
)
def test_experiment_sources_and_model_context_do_not_reveal_conclusion(
    scenario_id: str,
) -> None:
    registration = get_scenario(scenario_id, "1.0")
    generated = registration.generate_validated()
    source_text = generated.dataset.business_definitions.lower()
    model_text = registration.model_context_contract().model_dump_json().lower()

    assert "statistically significant" not in source_text
    assert "immaterial" not in source_text
    assert "expected_conclusion" not in model_text
    assert "statistical_expectation" not in model_text
    assert "ground_truth" not in model_text


@pytest.mark.parametrize(
    ("scenario_id", "rules"),
    (
        ("meaningful-ab-treatment-effect", meaningful_experiment_rules),
        ("no-effect-ab-experiment", no_effect_experiment_rules),
        (
            "significant-but-immaterial-ab-effect",
            immaterial_experiment_rules,
        ),
    ),
)
def test_statistical_evaluator_checks_typed_assessment(
    scenario_id: str,
    rules: Callable,
) -> None:
    registration = get_scenario(scenario_id, "1.1")
    generated = registration.generate_validated()
    assessment = statistical_assessment_for_scenario(
        generated.dataset, generated.definition
    )
    state = _state(
        specialist_results=[
            SpecialistResultRecord(
                agent_role="statistician",
                result=SpecialistResult(
                    objective="Assess the experiment",
                    statistical_assessments=[assessment],
                ),
            )
        ]
    )
    conclusion = {
        "meaningful-ab-treatment-effect": (
            "The treatment is statistically significant and practical."
        ),
        "no-effect-ab-experiment": (
            "The treatment is not statistically significant and the interval "
            "includes zero."
        ),
        "significant-but-immaterial-ab-effect": (
            "The treatment is statistically significant but immaterial and not "
            "practical."
        ),
    }[scenario_id]
    report = (
        "The confidence interval and effect size support the practical "
        "significance conclusion after checking each assumption; the randomized "
        "design supports a cautious causal interpretation. " + conclusion
    )
    checks = evaluate_statistics(state, report, rules().statistics_policy)
    assert all(check.status.value == "pass" for check in checks), checks


@pytest.mark.parametrize(
    "field_update",
    (
        {"conclusion": "not_statistically_significant"},
        {"confidence_level": 0.90},
        {"confidence_interval": ConfidenceInterval(lower=0.5, upper=0.6)},
        {"p_value": 0.5},
        {"effect_size": 0.0},
        {"practical_significance_threshold": 0.01},
        {"practically_significant": False},
        {"assumptions_checked": ("binary outcome",)},
        {"causal_interpretation": CausalInterpretation.ASSOCIATION_ONLY},
    ),
)
def test_statistical_evaluator_rejects_incomplete_or_wrong_claims(
    field_update: dict[str, object],
) -> None:
    registration = get_scenario("meaningful-ab-treatment-effect", "1.0")
    generated = registration.generate_validated()
    assessment = statistical_assessment_for_scenario(
        generated.dataset, generated.definition
    )
    altered = assessment.model_copy(update=field_update)
    state = _state(
        specialist_results=[
            SpecialistResultRecord(
                agent_role="statistician",
                result=SpecialistResult(
                    objective="Assess the experiment",
                    statistical_assessments=[altered],
                ),
            )
        ]
    )
    checks = evaluate_statistics(
        state,
        "confidence interval effect size practical significance assumptions causal "
        "statistically significant and practical",
        meaningful_experiment_rules().statistics_policy,
    )
    assert any(check.status.value == "fail" for check in checks)


_PROFILE_EVIDENCE = "evidence:table-profile"


def _audit(*issues: DataQualityIssue) -> AuditResult:
    return AuditResult(
        status=AuditStatus.COMPLETE,
        tables=[
            TableAudit(
                table_name="orders",
                row_count=1200,
                evidence_refs=[_PROFILE_EVIDENCE],
            )
        ],
        issues=list(issues),
    )


def _clean_audit() -> AuditResult:
    """A clean audit still has to show which checks it ran."""

    return AuditResult(
        status=AuditStatus.COMPLETE,
        tables=[
            TableAudit(
                table_name="orders",
                row_count=1200,
                evidence_refs=[_PROFILE_EVIDENCE],
            )
        ],
    )


def _executed_refs(state: AnalysisRunState) -> set[str]:
    """Treat every reference the fixture audit cites as executed evidence.

    Provenance resolution itself is covered by the offline adversarial fixtures;
    these scenario tests are about defect recall and false positives.
    """

    references = {_PROFILE_EVIDENCE}
    if state.audit is not None:
        references.update(
            reference
            for issue in state.audit.issues
            for reference in issue.evidence_refs
        )
    return references


def _state(**values: object) -> AnalysisRunState:
    return AnalysisRunState(
        run_id="task5-fixture",
        objective="Task 5 deterministic fixture",
        **values,
    )
