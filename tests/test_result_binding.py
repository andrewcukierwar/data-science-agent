"""P0.2: exact numerical propagation, with no model or paid calls."""

import json
from pathlib import Path

import pytest

from agents import AgentRole
from agents.analyst import persist_analyst_result
from agents.critic import validate_candidate_evidence_provenance
from agents.generalist import persist_generalist_result
from agents.lead import persist_lead_result
from agents.result_binding import ResultBindingError, resolve_result
from agents.statistician import persist_statistician_result
from evaluation.primitives import (
    StatisticsPolicy,
    _statistical_assessments,
    evaluate_provenance,
    evaluate_statistics,
)
from orchestration.ledger import AnalysisLedger
from orchestration.runner import AnalysisRunner
from sandbox.executor import SandboxExecutionResult
from schemas.audit import AuditResult
from schemas.computation import ComputationBinding, ComputedField
from schemas.findings import SpecialistResult
from schemas.generalist import GeneralistResult
from schemas.lead import LeadResult
from schemas.metrics import MetricComparison
from schemas.statistics import StatisticalAssessment, StatisticalExpectation
from schemas.validation import ValidationResult
from tests.test_agent_runtime import FakeExecutor

P_VALUE = 1.9504926019459696e-13
VALUES = {
    "confidence_level": 0.95,
    "estimate": 0.125,
    "confidence_interval": {"lower": 0.0625, "upper": 0.1875},
    "p_value": P_VALUE,
    "effect_size": 0.25,
    "practical_significance_threshold": 0.03125,
    "practically_significant": True,
}


def _context(tmp_path: Path, role=AgentRole.ANALYST):
    import pandas as pd

    from agents.runtime import AgentRunConfig, AgentRunContext
    from tools.artifacts import ArtifactManager
    from tools.python import PythonExecutionService
    from tools.sql import DuckDBExecutionService
    from tools.workspace import WorkspaceManager

    source = tmp_path / "source"
    source.mkdir(parents=True)
    pd.DataFrame({"revenue": [12.5]}).to_parquet(source / "orders.parquet")
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "binding", inputs_source=source
    )
    ledger = AnalysisLedger(workspace, objective="Compare the arms")
    return AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(workspace, ledger),
        python_service=PythonExecutionService(
            workspace, ledger, executor=FakeExecutor()
        ),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id="binding", agent_role=role, model="test-model"
        ),
    )


def _executed_statistic(tmp_path, role=AgentRole.STATISTICIAN, values=None):
    context = _context(tmp_path, role)
    # Retained-shaped execution fixture. This tests propagation, not a new method.
    values = values or VALUES
    context.python_service.executor = FakeExecutor(
        SandboxExecutionResult(
            success=True, stdout=json.dumps(values), exit_code=0, duration_seconds=0.1
        )
    )
    output = context.python_service.execute(
        "import pandas as pd\n"
        "frame = pd.read_parquet('/workspace/inputs/orders.parquet')\n"
        "# Retained-shaped statistical output supplied by the offline executor.\n",
        script_id="inference",
    )
    assert output.success
    binding = ComputationBinding(
        tool_event_id=output.tool_event_id,
        source="python_stdout_json",
        fields=[
            ComputedField(field=field, pointer="/" + field.replace(".", "/"))
            for field in StatisticalAssessment.numerical_fields
        ],
    )
    assessment = StatisticalAssessment(
        metric_key="response_difference",
        baseline_period="reference arm",
        comparison_period="assigned arm",
        method="retained inferential calculation",
        unit_of_analysis="participant",
        conclusion="significant_and_practical",
        assumptions_checked=("independent participants",),
        causal_interpretation="causal_effect_supported",
        evidence_refs=[output.tool_event_id],
        computation=binding,
        definition_context={
            "population": "eligible participants",
            "observation_window": "14 days",
        },
        caveats=[
            "Applies only to eligible participants; "
            "long-term outcomes were not measured."
        ],
    )
    return context, assessment


def _specialist(assessment):
    return SpecialistResult(
        objective="Compare the randomized arms.",
        statistical_assessments=[assessment],
        caveats=["The observation period is limited."],
    )


def _lead(**kwargs):
    return LeadResult(
        objective="Compare the arms.", answer="The assigned arm improved.", **kwargs
    )


def _expectation():
    return StatisticalExpectation(
        metric_key="response_difference",
        baseline_period="reference arm",
        comparison_period="assigned arm",
        expected_conclusion="significant_and_practical",
        expected_estimate=VALUES["estimate"],
        estimate_tolerance=0,
        expected_confidence_interval=VALUES["confidence_interval"],
        confidence_interval_tolerance=0,
        expected_p_value=P_VALUE,
        p_value_tolerance=0,
        expected_effect_size=VALUES["effect_size"],
        effect_size_tolerance=0,
        practical_significance_threshold=VALUES["practical_significance_threshold"],
        expected_practically_significant=True,
        required_assumptions=("independent participants",),
    )


@pytest.mark.parametrize(
    "role,persist",
    [
        (AgentRole.ANALYST, persist_analyst_result),
        (AgentRole.STATISTICIAN, persist_statistician_result),
    ],
)
def test_corrupted_p_value_rejected_by_both_specialists(tmp_path, role, persist):
    context, assessment = _executed_statistic(tmp_path, role)
    corrupted = assessment.model_copy(update={"p_value": 1.0})
    with pytest.raises(ResultBindingError, match="p_value contradicts"):
        persist(_specialist(corrupted), context)
    assert not context.ledger.statistical_assessments
    assert not context.ledger.specialist_results


@pytest.mark.parametrize(
    "role,persist",
    [
        (AgentRole.ANALYST, persist_analyst_result),
        (AgentRole.STATISTICIAN, persist_statistician_result),
    ],
)
def test_exact_specialist_lead_critic_report_evaluator_round_trip(
    tmp_path, role, persist
):
    context, assessment = _executed_statistic(tmp_path, role)
    specialist = persist(_specialist(assessment), context)
    bound = specialist.statistical_assessments[0]
    assert bound.p_value == P_VALUE
    context.ledger.record_specialist_result(role.value, specialist)
    selected = persist_lead_result(
        _lead(selected_result_ids=[bound.result_id]), context
    )
    assert selected.statistical_assessments == [bound]
    candidate = AnalysisRunner._candidate(selected.objective, selected)
    assert candidate.statistical_assessments == [bound]
    assert "observation period" in candidate.caveats[0]
    assert validate_candidate_evidence_provenance(candidate, context.ledger) is None
    context.ledger = AnalysisLedger(context.workspace)
    assert (
        context.ledger.statistical_assessments[0].model_dump_json()
        == bound.model_dump_json()
    )
    report = AnalysisRunner._render_report(
        selected.objective,
        AuditResult(status="complete"),
        selected,
        ValidationResult(status="pass"),
        constrained=False,
        constraint_reason=None,
        ledger=context.ledger,
    )
    for value in [P_VALUE, VALUES["estimate"], 0.0625, 0.1875, 0.95, 0.25, 0.03125]:
        assert str(value) in report
    for phrase in [
        bound.result_id,
        "eligible participants",
        "14 days",
        "long-term",
        "observation period",
    ]:
        assert phrase in report
    checks = evaluate_statistics(
        context.ledger.state, report, StatisticsPolicy(expectations=(_expectation(),))
    )
    assert all(check.status.value == "pass" for check in checks), checks
    provenance = evaluate_provenance(context.workspace, context.ledger.state, report)
    binding_checks = [check for check in provenance if ":binding:" in check.check_id]
    assert len(binding_checks) == 1 and binding_checks[0].status.value == "pass"
    corrupted = bound.model_copy(update={"p_value": 1.0})
    context.ledger._state.statistical_assessments = [corrupted]
    context.ledger.save()
    checks = evaluate_provenance(context.workspace, context.ledger.state, report)
    assert any(
        check.status.value == "fail" and ":binding:" in check.check_id
        for check in checks
    )


@pytest.mark.parametrize("generalist", [False, True])
def test_both_finalizers_reject_corruption_and_hydrate_reference_only(
    tmp_path, generalist
):
    context, assessment = _executed_statistic(
        tmp_path, AgentRole.GENERALIST if generalist else AgentRole.LEAD
    )

    def finalize(item):
        candidate = _lead(statistical_assessments=[item])
        if generalist:
            return persist_generalist_result(
                GeneralistResult(
                    audit=AuditResult(status="complete"),
                    candidate=candidate,
                    validation=ValidationResult(status="pass"),
                ),
                context,
            ).candidate
        return persist_lead_result(candidate, context)

    with pytest.raises(ResultBindingError, match="p_value contradicts"):
        finalize(assessment.model_copy(update={"p_value": 1.0}))
    assert not context.ledger.statistical_assessments
    result = finalize(assessment)
    assert result.statistical_assessments[0].p_value == P_VALUE


def test_metric_uses_exact_sql_cell_and_rejects_different_value(tmp_path):
    context = _context(tmp_path)
    output = context.sql_service.execute(
        "SELECT SUM(revenue) FROM orders", query_id="sum"
    )
    metric = MetricComparison(
        metric_key="receipts",
        baseline_period="observed",
        comparison_period="observed",
        comparison_type="level",
        unit="currency",
        evidence_refs=[output.tool_event_id],
        computation=ComputationBinding(
            tool_event_id=output.tool_event_id,
            source="sql_rows",
            fields=[ComputedField(field="value", pointer="/0/0")],
        ),
    )
    assert resolve_result(metric, context.ledger).value == 12.5
    with pytest.raises(ResultBindingError, match="value contradicts"):
        persist_lead_result(
            _lead(metric_comparisons=[metric.model_copy(update={"value": 99.0})]),
            context,
        )
    result = persist_analyst_result(
        SpecialistResult(objective="Sum receipts", metric_comparisons=[metric]), context
    )
    context.ledger.record_specialist_result("analyst", result)
    selected = persist_lead_result(
        _lead(selected_result_ids=[result.metric_comparisons[0].result_id]), context
    )
    assert selected.metric_comparisons == result.metric_comparisons


def test_corrected_selection_preserves_history_and_unresolved_conflicts_fail(tmp_path):
    context, correct = _executed_statistic(tmp_path)
    # A historical, legitimately different computation (not a transcription error).
    event = context.ledger.tool_events[-1]
    old_values = {**VALUES, "p_value": 0.5}
    old_event = event.model_copy(
        update={
            "id": "previous-execution",
            "output": {**event.output, "stdout": json.dumps(old_values)},
        }
    )
    context.ledger.append_tool_event(old_event)
    old = correct.model_copy(
        update={
            "evidence_refs": [old_event.id],
            "computation": correct.computation.model_copy(
                update={"tool_event_id": old_event.id}
            ),
        }
    )
    old = resolve_result(old, context.ledger)
    correct = resolve_result(correct, context.ledger)
    context.ledger.record_specialist_result("statistician", _specialist(old))
    persist_lead_result(_lead(statistical_assessments=[old]), context)
    persist_lead_result(_lead(statistical_assessments=[correct]), context)
    reloaded = AnalysisLedger(context.workspace)
    assert reloaded.state.statistical_assessment_history == [old, correct]
    assert _statistical_assessments(reloaded.state) == (correct,)
    checks = evaluate_statistics(
        reloaded.state, "", StatisticsPolicy(expectations=(_expectation(),))
    )
    assert all(check.status.value == "pass" for check in checks)
    with pytest.raises(ResultBindingError, match="conflicting selected"):
        persist_lead_result(_lead(statistical_assessments=[old, correct]), context)
    assert context.ledger.statistical_assessments == [correct]
    persist_lead_result(_lead(), context)
    assert _statistical_assessments(context.ledger.state) == ()
    assert context.ledger.state.statistical_assessment_history == [old, correct]


def test_unbound_legacy_is_readable_but_cannot_be_selected_as_bound(tmp_path):
    context, assessment = _executed_statistic(tmp_path)
    bound = resolve_result(assessment, context.ledger)
    legacy = bound.model_copy(update={"computation": None, "result_id": None})
    assert StatisticalAssessment.model_validate_json(legacy.model_dump_json()) == legacy
    with pytest.raises(ResultBindingError, match="unbound"):
        persist_lead_result(_lead(statistical_assessments=[legacy]), context)
    context.ledger._state.schema_version = "1.1"
    context.ledger.record_specialist_result("statistician", _specialist(legacy))
    assert _statistical_assessments(context.ledger.state) == (legacy,)
    assert resolve_result(legacy, context.ledger).result_id is None


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "duplicate",
        "pointer",
        "failed",
        "truncated",
        "alias",
        "string",
        "wrong_source",
        "duplicate_json",
    ],
)
def test_binding_rejects_incomplete_ambiguous_or_unusable_sources(tmp_path, damage):
    context, assessment = _executed_statistic(tmp_path)
    binding = assessment.computation
    event = context.ledger.tool_events[-1]
    if damage == "missing":
        binding = binding.model_copy(update={"fields": binding.fields[:-1]})
    elif damage == "duplicate":
        binding = binding.model_copy(
            update={"fields": [*binding.fields, binding.fields[0]]}
        )
    elif damage == "pointer":
        binding = binding.model_copy(
            update={
                "fields": [
                    binding.fields[0].model_copy(update={"pointer": "/missing"}),
                    *binding.fields[1:],
                ]
            }
        )
    elif damage == "failed":
        event.status = "failed"
        event.error = "test failure"
    elif damage == "truncated":
        event.output["stdout_truncated"] = True
    elif damage == "alias":
        binding = binding.model_copy(update={"tool_event_id": "inference"})
    elif damage == "string":
        event.output["stdout"] = json.dumps({**VALUES, "p_value": str(P_VALUE)})
    elif damage == "wrong_source":
        binding = binding.model_copy(update={"source": "sql_rows"})
    elif damage == "duplicate_json":
        event.output["stdout"] = '{"p_value": 1, "p_value": 2}'
    with pytest.raises(ResultBindingError):
        resolve_result(
            assessment.model_copy(update={"computation": binding}), context.ledger
        )


@pytest.mark.parametrize(
    "role,persist",
    [
        (AgentRole.ANALYST, persist_analyst_result),
        (AgentRole.STATISTICIAN, persist_statistician_result),
    ],
)
def test_both_specialists_validate_statistical_provenance(tmp_path, role, persist):
    context, assessment = _executed_statistic(tmp_path, role)
    invalid = assessment.model_copy(update={"evidence_refs": ["invented-event"]})
    with pytest.raises(ResultBindingError, match="provenance"):
        persist(_specialist(invalid), context)
    script = context.workspace.working / "scripts" / "inference.py"
    script.write_text("print('hardcoded output')\n")
    with pytest.raises(ResultBindingError, match="source lineage"):
        persist(_specialist(assessment), context)


def test_critic_request_contains_selected_statistics_and_rejects_mutation(
    tmp_path, monkeypatch
):
    import asyncio
    from types import SimpleNamespace

    from agents.critic import run_critic

    context, assessment = _executed_statistic(tmp_path, AgentRole.CRITIC)
    result = persist_lead_result(_lead(statistical_assessments=[assessment]), context)
    candidate = AnalysisRunner._candidate(result.objective, result)
    requests = []

    async def fake_run(agent, prompt, **kwargs):
        requests.append(prompt)
        return SimpleNamespace(final_output=ValidationResult(status="pass"))

    monkeypatch.setattr("agents.critic.run_agent_with_usage", fake_run)
    validation = asyncio.run(run_critic(context, candidate))
    assert validation.status.value == "pass"
    assert len(requests) == 1
    assert str(P_VALUE) in requests[0]
    assert "statistical_assessments" in requests[0]
    assert "long-term outcomes" in requests[0]
    damaged = candidate.model_copy(
        update={
            "statistical_assessments": [
                candidate.statistical_assessments[0].model_copy(update={"p_value": 1.0})
            ]
        }
    )
    validation = asyncio.run(run_critic(context, damaged))
    assert validation.status.value == "revise"
    assert len(requests) == 1  # Invalid bound numbers never reach a model review.


def test_two_selected_bound_metrics_cannot_be_silently_deduplicated(tmp_path):
    context, assessment = _executed_statistic(tmp_path, AgentRole.LEAD)
    metrics = [
        MetricComparison(
            metric_key="arm_change",
            baseline_period="reference",
            comparison_period="assigned",
            comparison_type="absolute_difference",
            unit="fraction",
            evidence_refs=assessment.evidence_refs,
            computation=ComputationBinding(
                tool_event_id=assessment.computation.tool_event_id,
                source="python_stdout_json",
                fields=[ComputedField(field="value", pointer=pointer)],
            ),
        )
        for pointer in ["/estimate", "/effect_size"]
    ]
    with pytest.raises(ResultBindingError, match="conflicting selected metric"):
        persist_lead_result(_lead(metric_comparisons=metrics), context)
    assert not context.ledger.metric_comparisons


def test_metric_rejects_lossy_numeric_coercion_and_forged_identity(tmp_path):
    context, assessment = _executed_statistic(tmp_path)
    event = context.ledger.tool_events[-1]
    event.output["stdout"] = json.dumps({"large_integer": 2**53 + 1})
    metric = MetricComparison(
        metric_key="large_count",
        baseline_period="observed",
        comparison_period="observed",
        comparison_type="level",
        unit="count",
        evidence_refs=[event.id],
        computation=ComputationBinding(
            tool_event_id=event.id,
            source="python_stdout_json",
            fields=[ComputedField(field="value", pointer="/large_integer")],
        ),
    )
    with pytest.raises(ResultBindingError, match="represented exactly"):
        resolve_result(metric, context.ledger)
    event.output["stdout"] = json.dumps(VALUES)
    with pytest.raises(ResultBindingError, match="result_id"):
        resolve_result(
            assessment.model_copy(update={"result_id": "invented-result"}),
            context.ledger,
        )


def test_offline_engine_versions_selected_contract_without_touching_rules(tmp_path):
    from evaluation.engine import ScenarioRules, evaluate_workspace

    context, assessment = _executed_statistic(tmp_path)
    persist_lead_result(_lead(statistical_assessments=[assessment]), context)
    rules = ScenarioRules(
        scenario_id="binding-fixture",
        scenario_version="1.0",
        evaluator_version="1.0",
        statistics_policy=StatisticsPolicy(expectations=(_expectation(),)),
    )
    before = context.ledger.state_path.read_bytes()
    evaluation = evaluate_workspace(context.workspace, rules)
    assert evaluation.result.numerical_result_contract_version == "1.0"
    assert evaluation.result.evaluator_version == "1.0"
    assert all(
        check.status.value == "pass"
        for check in evaluation.checks
        if check.check_id.startswith("statistics:")
    )
    assert context.ledger.state_path.read_bytes() == before
    context.ledger.state.schema_version = "1.1"
    legacy = context.ledger.statistical_assessments[0].model_copy(
        update={"computation": None, "result_id": None}
    )
    context.ledger.state.statistical_assessments = [legacy]
    context.ledger.save()
    evaluation = evaluate_workspace(context.workspace, rules)
    assert evaluation.result.numerical_result_contract_version is None
    assert not any(":binding:" in check.check_id for check in evaluation.checks)


def test_numerical_finding_cannot_bypass_binding(tmp_path):
    from schemas.findings import Finding

    context, assessment = _executed_statistic(tmp_path, AgentRole.ANALYST)
    finding = Finding(
        id="effect",
        statement="The observed effect is positive.",
        metric="arm_change",
        confidence="high",
        evidence_refs=assessment.evidence_refs,
        computation=ComputationBinding(
            tool_event_id=assessment.computation.tool_event_id,
            source="python_stdout_json",
            fields=[ComputedField(field="value", pointer="/estimate")],
        ),
    )
    returned = persist_analyst_result(
        SpecialistResult(objective="Compare arms", findings=[finding]), context
    )
    bound = returned.findings[0]
    assert bound.value == VALUES["estimate"]
    selected = persist_lead_result(_lead(findings=[bound]), context)
    assert selected.findings == [bound]
    with pytest.raises(ResultBindingError, match="value contradicts"):
        persist_lead_result(
            _lead(findings=[bound.model_copy(update={"value": 1.0})]), context
        )
    with pytest.raises(ResultBindingError, match="unbound"):
        persist_lead_result(
            _lead(
                findings=[
                    bound.model_copy(update={"computation": None, "result_id": None})
                ]
            ),
            context,
        )
