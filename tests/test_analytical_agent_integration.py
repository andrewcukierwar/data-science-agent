"""P1.1b agent-tool integration without model calls or benchmark fixtures."""

import asyncio
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from agents.tool_context import ToolContext

from agents import (
    AgentRole,
    AgentRunConfig,
    AgentRunContext,
    ToolOutputText,
    ToolResponse,
    allowed_analytical_operations_for_role,
    run_analytical,
    run_sql,
    tools_for_role,
)
from agents.critic import validate_candidate_evidence_provenance
from agents.generalist import persist_generalist_result
from agents.lead import persist_lead_result
from agents.statistician import persist_statistician_result
from evaluation.primitives import StatisticsPolicy, evaluate_statistics
from orchestration.ledger import AnalysisLedger
from orchestration.runner import AnalysisRunner
from schemas.analytical import AnalyticalToolOutput
from schemas.audit import AuditResult
from schemas.computation import ComputationBinding, ComputedField
from schemas.findings import SpecialistResult
from schemas.generalist import GeneralistResult
from schemas.lead import LeadResult
from schemas.statistics import StatisticalAssessment, StatisticalExpectation
from schemas.validation import ValidationResult
from tools.artifacts import ArtifactManager
from tools.python import PythonExecutionService
from tools.sql import DuckDBExecutionService
from tools.workspace import WorkspaceManager

START = date(2043, 5, 17)


def _day(offset: int) -> date:
    return START + timedelta(days=offset)


def _context(
    tmp_path: Path,
    role: AgentRole,
    tables: dict[str, dict[str, list]],
    *,
    run_id: str = "analytical-agent",
    max_sql_rows: int = 100_000,
    max_text_chars: int = 100_000,
) -> AgentRunContext:
    source = tmp_path / f"source-{run_id}"
    source.mkdir()
    for name, columns in tables.items():
        pq.write_table(pa.table(columns), source / f"{name}.parquet")
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        run_id, inputs_source=source
    )
    ledger = AnalysisLedger(workspace, objective="Evaluate a bounded fixture")
    return AgentRunContext(
        workspace=workspace,
        ledger=ledger,
        sql_service=DuckDBExecutionService(
            workspace, ledger, max_rows=max_sql_rows
        ),
        python_service=PythonExecutionService(workspace, ledger),
        artifact_manager=ArtifactManager(workspace, ledger),
        run_config=AgentRunConfig(
            run_id=run_id,
            agent_role=role,
            model="test-model",
            max_text_chars=max_text_chars,
        ),
    )


def _invoke(tool, context: AgentRunContext, arguments: dict) -> ToolResponse:  # noqa: ANN001
    payload = json.dumps(arguments, default=str)
    wrapper = ToolContext(
        context,
        tool_name=tool.name,
        tool_call_id=f"call-{tool.name}",
        tool_arguments=payload,
    )
    result = asyncio.run(tool.on_invoke_tool(wrapper, payload))
    assert isinstance(result, ToolOutputText)
    return ToolResponse.model_validate_json(result.text)


def _sql(context: AgentRunContext, relation: str, query_id: str) -> str:
    response = _invoke(
        run_sql,
        context,
        {"sql": f'SELECT * FROM "{relation}"', "query_id": query_id},
    )
    assert response.success, response
    return response.data["tool_event_id"]


def _base_request(operation: str, source_event: str) -> dict:
    return {
        "operation": operation,
        "population": "eligible fixture units",
        "grain": "one row per fixture unit",
        "period": {"start": _day(0), "end": _day(4)},
        "numeric": {"projection": "nearest_binary64"},
        "source": {"tool_event_id": source_event, "relation": "fixture input"},
    }


def _analytical(context: AgentRunContext, request: dict) -> AnalyticalToolOutput:
    response = _invoke(run_analytical, context, {"request": request})
    assert response.success, response
    return AnalyticalToolOutput.model_validate(response.data)


def test_schema_role_surfaces_and_runtime_operation_guards(tmp_path: Path) -> None:
    operation_sets = {
        role: allowed_analytical_operations_for_role(role) for role in AgentRole
    }
    specialist_union = (
        operation_sets[AgentRole.DATA_AUDITOR]
        | operation_sets[AgentRole.ANALYST]
        | operation_sets[AgentRole.STATISTICIAN]
    )
    assert operation_sets[AgentRole.DATA_AUDITOR] == {"coverage"}
    assert operation_sets[AgentRole.ANALYST] == {
        "entity_aggregate",
        "ratio",
        "contrast",
        "reconciliation",
    }
    assert operation_sets[AgentRole.STATISTICIAN] == {"binary_experiment"}
    assert operation_sets[AgentRole.GENERALIST] == specialist_union
    assert operation_sets[AgentRole.LEAD] == operation_sets[AgentRole.CRITIC] == set()
    for role in AgentRole:
        names = {tool.name for tool in tools_for_role(role)}
        assert ("run_analytical" in names) is bool(operation_sets[role])

    schema = run_analytical.params_json_schema
    request_schema = schema["properties"]["request"]
    assert run_analytical.strict_json_schema is False
    assert request_schema["discriminator"]["propertyName"] == "operation"
    assert set(request_schema["discriminator"]["mapping"]) == specialist_union
    assert "leave numerical fields" in run_analytical.description
    assert "analytical record ID" in run_analytical.description

    context = _context(
        tmp_path,
        AgentRole.DATA_AUDITOR,
        {
            "signals": {
                "observed_on": [_day(0), _day(1)],
                "unit_code": ["a", "b"],
                "arm_name": ["old", "new"],
                "outcome_flag": [0, 1],
            }
        },
    )
    source_event = _sql(context, "signals", "source-for-guard")
    binary = {
        **_base_request("binary_experiment", source_event),
        "subject_id": "unit_code",
        "assignment": "arm_name",
        "temporal_field": "observed_on",
        "control": "old",
        "treatment": "new",
        "outcome": "outcome_flag",
        "confidence_level": 0.95,
        "practical_threshold": "0.1",
        "design_assumptions": ["fixture assignments are independent"],
    }
    response = _invoke(run_analytical, context, {"request": binary})
    assert response.success is False
    assert response.error.code == "permission_denied"
    assert context.ledger.budget.python_executions == 0
    assert not any(e.tool_name == "run_analytical" for e in context.ledger.tool_events)


def test_auditor_coverage_distinguishes_expected_gap_from_sparse_events(
    tmp_path: Path,
) -> None:
    context = _context(
        tmp_path,
        AgentRole.DATA_AUDITOR,
        {"telemetry_stream": {"arrived_on": [_day(0), _day(2)]}},
    )
    source_event = _sql(context, "telemetry_stream", "coverage-source")
    base = {
        **_base_request("coverage", source_event),
        "temporal_field": "arrived_on",
    }
    sparse = _analytical(context, base)
    expected = _analytical(context, {**base, "cadence": "daily"})

    assert sparse.record.results[0].quantities["missing_cell_count"].value is None
    assert "not assessed" in sparse.record.results[0].warnings[0]
    assert expected.record.results[0].quantities["missing_cell_count"].value == 2
    assert expected.record.results[0].details["whole_missing_dates"] == [
        _day(1).isoformat(),
        _day(3).isoformat(),
    ]


@pytest.mark.parametrize("source_state", ["truncated", "failed", "ambiguous"])
def test_incomplete_or_unusable_sql_sources_are_rejected(
    tmp_path: Path, source_state: str
) -> None:
    context = _context(
        tmp_path,
        AgentRole.DATA_AUDITOR,
        {"pulse_rows": {"seen_on": [_day(0), _day(1)]}},
        max_sql_rows=1 if source_state == "truncated" else 100_000,
    )
    if source_state == "failed":
        sql_response = _invoke(
            run_sql,
            context,
            {"sql": "SELECT * FROM absent_relation", "query_id": "bad-source"},
        )
        assert sql_response.success is False
        source_event = sql_response.data["tool_event_id"]
    else:
        source_event = _sql(context, "pulse_rows", "source")
        if source_state == "ambiguous":
            event = next(e for e in context.ledger.tool_events if e.id == source_event)
            event.output["columns"] = ["seen_on", "seen_on"]
            event.output["column_types"] = ["DATE", "DATE"]
            event.output["rows"] = [[_day(0), _day(0)], [_day(1), _day(1)]]
            context.ledger.save()
    response = _invoke(
        run_analytical,
        context,
        {
            "request": {
                **_base_request("coverage", source_event),
                "temporal_field": "seen_on",
                "cadence": "daily",
            }
        },
    )
    assert response.success is False
    assert response.data["tool_event_id"].startswith("tool-analytical-")
    assert any(
        event.id == response.data["tool_event_id"]
        and event.tool_name == "run_analytical"
        and event.status.value == "failed"
        for event in context.ledger.tool_events
    )


def test_large_model_response_keeps_ids_and_explicit_inspection_route(
    tmp_path: Path,
) -> None:
    context = _context(
        tmp_path,
        AgentRole.DATA_AUDITOR,
        {"bounded_feed": {"day_mark": [_day(0), _day(2)]}},
        max_text_chars=600,
    )
    source_event = _sql(context, "bounded_feed", "bounded-source")
    response = _invoke(
        run_analytical,
        context,
        {
            "request": {
                **_base_request("coverage", source_event),
                "temporal_field": "day_mark",
                "cadence": "daily",
            }
        },
    )
    assert response.success is True
    output = AnalyticalToolOutput.model_validate(response.data)
    assert output.record is None
    assert output.record_included is False
    assert output.tool_event_id == output.inspect_reference
    assert output.analytical_record_id.startswith("analytical-")
    assert output.result_summaries_truncated or output.result_summaries
    inspected = _invoke(
        next(t for t in tools_for_role(AgentRole.DATA_AUDITOR) if t.name == "inspect_evidence"),
        context,
        {"reference": output.inspect_reference},
    )
    assert inspected.success is True
    assert inspected.data["tool_event_id"] == output.tool_event_id


def test_analyst_entity_first_ratio_contrast_and_reconciliation(
    tmp_path: Path,
) -> None:
    context = _context(
        tmp_path,
        AgentRole.ANALYST,
        {
            "members_alpha": {
                "member_code": ["m1", "m2", "m3"],
                "entry_day": [_day(0), _day(0), _day(0)],
                "cohort_band": ["north", "north", "south"],
            },
            "touches_beta": {
                "touch_code": [1, 2, 3, 4],
                "member_link": ["m1", "m1", "m1", "m2"],
                "touch_day": [_day(0), _day(1), _day(3), _day(-1)],
                "credit_amount": [
                    Decimal("10.10"),
                    Decimal("20.20"),
                    Decimal("999.00"),
                    Decimal("400.00"),
                ],
            },
            "ledger_gamma": {
                "row_code": ["r1", "r2"],
                "booked_day": [_day(0), _day(1)],
                "reported": [100.0000000000001, 100.02],
                "gross_part": [150.0, 150.0],
                "offset_part": [50.0, 50.0],
            },
        },
    )
    members = _sql(context, "members_alpha", "members")
    touches = _sql(context, "touches_beta", "touches")
    ledger = _sql(context, "ledger_gamma", "ledger")
    aggregate = _analytical(
        context,
        {
            "operation": "entity_aggregate",
            "population": "members entering the shifted fixture cohort",
            "grain": "member_code",
            "period": {"start": _day(0), "end": _day(4)},
            "numeric": {"projection": "nearest_binary64"},
            "entities": {"tool_event_id": members, "relation": "cohort members"},
            "entity_key": "member_code",
            "cohort_date": "entry_day",
            "events": {"tool_event_id": touches, "relation": "member touches"},
            "event_key": "touch_code",
            "event_entity_key": "member_link",
            "event_date": "touch_day",
            "window": {
                "start_day": 0,
                "end_day": 2,
                "observed_until": _day(6),
                "maturity": "require_complete",
            },
            "dimensions": {},
            "include_zero_activity": True,
            "numerator": {
                "kind": "event_sum",
                "semantic_key": "in-window credited amount",
                "unit": "credits",
                "column": "credit_amount",
            },
            "denominator": {
                "kind": "entity_count",
                "semantic_key": "eligible member",
                "unit": "members",
            },
            "aggregation": "ratio_of_sums",
            "unit": "credits/member",
        },
    )
    result = aggregate.record.results[0]
    assert result.quantities["value"].value == 10.1
    assert result.quantities["included_entities"].value == 3
    assert result.quantities["zero_activity_entities"].value == 2
    assert result.quantities["qualifying_events"].value == 2

    ratio = _analytical(
        context,
        {
            "operation": "ratio",
            "numerator": {
                "tool_event_id": aggregate.tool_event_id,
                "quantity": "numerator_sum",
            },
            "denominator": {
                "tool_event_id": aggregate.tool_event_id,
                "quantity": "denominator_sum",
            },
            "unit": "credits/member",
            "semantic_key": "independently selected component ratio",
            "numeric": {"projection": "nearest_binary64"},
        },
    )
    level = _analytical(
        context,
        {
            "operation": "contrast",
            "baseline": {"tool_event_id": ratio.tool_event_id},
            "comparison_type": "level",
            "numeric": {"projection": "nearest_binary64"},
        },
    )
    assert ratio.record.results[0].quantities["value"].value == 10.1
    assert level.record.results[0].quantities["value"].value == 10.1

    reconciliation = _analytical(
        context,
        {
            **_base_request("reconciliation", ledger),
            "row_key": "row_code",
            "temporal_field": "booked_day",
            "result_column": "reported",
            "components": {"gross_part": "1", "offset_part": "-1"},
            "unit": "credits",
            "absolute_tolerance": "0.005",
            "relative_tolerance": "0",
        },
    )
    reconciled = reconciliation.record.results[0]
    assert reconciled.quantities["discrepancy_count"].value == 1
    assert reconciled.details["violating_keys"] == ["r2"]


def _experiment_context(
    tmp_path: Path,
    role: AgentRole,
    control_successes: int,
    treatment_successes: int,
    *,
    arm_size: int,
    run_id: str,
) -> AgentRunContext:
    return _context(
        tmp_path,
        role,
        {
            "assignment_log": {
                "participant_token": [f"p{i}" for i in range(arm_size * 2)],
                "assigned_on": [_day(0)] * (arm_size * 2),
                "variant_label": ["baseline-x"] * arm_size
                + ["candidate-y"] * arm_size,
                "success_bit": [1] * control_successes
                + [0] * (arm_size - control_successes)
                + [1] * treatment_successes
                + [0] * (arm_size - treatment_successes),
            }
        },
        run_id=run_id,
    )


def _binary_output(context: AgentRunContext, threshold: str) -> AnalyticalToolOutput:
    if context.agent_role is AgentRole.STATISTICIAN:
        context.enter_nested_role(AgentRole.ANALYST)
        try:
            source_event = _sql(context, "assignment_log", "experiment-source")
        finally:
            context.exit_nested_role(AgentRole.ANALYST)
        context.assert_base_role(AgentRole.STATISTICIAN)
    else:
        source_event = _sql(context, "assignment_log", "experiment-source")
    return _analytical(
        context,
        {
            **_base_request("binary_experiment", source_event),
            "subject_id": "participant_token",
            "assignment": "variant_label",
            "temporal_field": "assigned_on",
            "control": "baseline-x",
            "treatment": "candidate-y",
            "outcome": "success_bit",
            "confidence_level": 0.95,
            "practical_threshold": threshold,
            "design_assumptions": [
                "participant assignments are independent in this fixture"
            ],
        },
    )


def _assessment(
    output: AnalyticalToolOutput, conclusion: str
) -> StatisticalAssessment:
    pointers = {
        item.quantity: item.pointer
        for item in output.binding_pointers
        if item.value_available
    }
    quantities = {
        "confidence_level": "confidence_level",
        "estimate": "estimate",
        "confidence_interval.lower": "confidence_interval_lower",
        "confidence_interval.upper": "confidence_interval_upper",
        "p_value": "p_value",
        "effect_size": "effect_size",
        "practical_significance_threshold": "practical_significance_threshold",
        "practically_significant": "practically_significant",
    }
    return StatisticalAssessment(
        metric_key="binary response difference",
        baseline_period="baseline-x",
        comparison_period="candidate-y",
        method=output.record.results[0].method,
        unit_of_analysis="participant_token",
        conclusion=conclusion,
        assumptions_checked=(
            "participant assignments are independent in this fixture",
        ),
        causal_interpretation="association_only",
        evidence_refs=[output.tool_event_id],
        computation=ComputationBinding(
            tool_event_id=output.tool_event_id,
            source="analytical_record",
            fields=[
                ComputedField(field=field, pointer=pointers[quantity])
                for field, quantity in quantities.items()
            ],
        ),
    )


@pytest.mark.parametrize(
    (
        "control_successes",
        "treatment_successes",
        "arm_size",
        "threshold",
        "conclusion",
        "estimate",
        "p_value",
        "lower",
        "upper",
        "effect_size",
        "practical",
    ),
    [
        (
            40,
            60,
            100,
            "0.05",
            "significant_and_practical",
            0.2,
            0.004677734981047266,
            0.06420971191085941,
            0.3357902880891406,
            0.40271584158066176,
            True,
        ),
        (
            40,
            40,
            100,
            "0.05",
            "not_statistically_significant",
            0.0,
            1.0,
            -0.1357902880891406,
            0.1357902880891406,
            0.0,
            False,
        ),
        (
            5000,
            5200,
            10_000,
            "0.05",
            "significant_but_immaterial",
            0.02,
            0.0046694723203575105,
            0.006146506480967404,
            0.033853493519032635,
            0.04001067435398986,
            False,
        ),
    ],
)
def test_binary_tool_values_survive_specialist_lead_critic_report_and_evaluator(
    tmp_path: Path,
    control_successes: int,
    treatment_successes: int,
    arm_size: int,
    threshold: str,
    conclusion: str,
    estimate: float,
    p_value: float,
    lower: float,
    upper: float,
    effect_size: float,
    practical: bool,
) -> None:
    context = _experiment_context(
        tmp_path,
        AgentRole.STATISTICIAN,
        control_successes,
        treatment_successes,
        arm_size=arm_size,
        run_id=f"experiment-{control_successes}-{treatment_successes}",
    )
    output = _binary_output(context, threshold)
    assessment = _assessment(output, conclusion)
    specialist = persist_statistician_result(
        SpecialistResult(
            objective="Assess the fixture arm difference.",
            statistical_assessments=[assessment],
            caveats=["The fixture design assumptions are externally supplied."],
        ),
        context,
    )
    bound = specialist.statistical_assessments[0]
    assert bound.result_id != output.analytical_record_id
    assert bound.estimate == estimate
    assert bound.p_value == pytest.approx(p_value, rel=1e-7)
    assert bound.confidence_interval.lower == pytest.approx(lower, abs=1e-8)
    assert bound.confidence_interval.upper == pytest.approx(upper, abs=1e-8)
    assert bound.effect_size == pytest.approx(effect_size, abs=1e-10)
    assert bound.practically_significant is practical

    context.ledger.record_specialist_result("statistician", specialist)
    selected = persist_lead_result(
        LeadResult(
            objective="Assess the fixture arm difference.",
            answer="The retained binary assessment answers the bounded question.",
            selected_result_ids=[bound.result_id],
        ),
        context,
    )
    assert selected.statistical_assessments == [bound]
    candidate = AnalysisRunner._candidate(selected.objective, selected)
    assert validate_candidate_evidence_provenance(candidate, context.ledger) is None
    report = AnalysisRunner._render_report(
        selected.objective,
        AuditResult(status="complete"),
        selected,
        ValidationResult(status="pass"),
        constrained=False,
        constraint_reason=None,
        ledger=context.ledger,
    )
    expected = StatisticalExpectation(
        metric_key="binary response difference",
        baseline_period="baseline-x",
        comparison_period="candidate-y",
        expected_conclusion=conclusion,
        expected_estimate=estimate,
        estimate_tolerance=0,
        expected_confidence_interval={"lower": lower, "upper": upper},
        confidence_interval_tolerance=1e-8,
        expected_p_value=p_value,
        p_value_tolerance=1e-12,
        expected_effect_size=effect_size,
        effect_size_tolerance=1e-10,
        practical_significance_threshold=float(threshold),
        expected_practically_significant=practical,
        required_assumptions=(
            "participant assignments are independent in this fixture",
        ),
        expected_causal_interpretation="association_only",
    )
    checks = evaluate_statistics(
        context.ledger.state,
        report,
        StatisticsPolicy(expectations=(expected,)),
    )
    assert all(check.status.value == "pass" for check in checks), checks
    for value in (
        bound.estimate,
        bound.p_value,
        bound.confidence_interval.lower,
        bound.confidence_interval.upper,
        bound.effect_size,
        bound.practical_significance_threshold,
    ):
        assert str(value) in report


@pytest.mark.parametrize(
    ("control_successes", "treatment_successes", "arm_size", "conclusion"),
    [
        (40, 60, 100, "significant_and_practical"),
        (40, 40, 100, "not_statistically_significant"),
        (5000, 5200, 10_000, "significant_but_immaterial"),
    ],
)
def test_generalist_uses_the_same_bound_finalizer_path(
    tmp_path: Path,
    control_successes: int,
    treatment_successes: int,
    arm_size: int,
    conclusion: str,
) -> None:
    context = _experiment_context(
        tmp_path,
        AgentRole.GENERALIST,
        control_successes,
        treatment_successes,
        arm_size=arm_size,
        run_id=f"generalist-{control_successes}-{treatment_successes}",
    )
    output = _binary_output(context, "0.05")
    unbound = _assessment(output, conclusion)
    finalized = persist_generalist_result(
        GeneralistResult(
            audit=AuditResult(status="complete"),
            candidate=LeadResult(
                objective="Assess one bounded fixture.",
                answer="The bound result is retained.",
                statistical_assessments=[unbound],
            ),
            validation=ValidationResult(status="pass"),
        ),
        context,
    )
    bound = finalized.candidate.statistical_assessments[0]
    assert bound.result_id is not None
    assert bound.result_id != output.analytical_record_id
    quantities = output.record.results[0].quantities
    assert bound.estimate == quantities["estimate"].value
    assert bound.p_value == quantities["p_value"].value
    assert bound.practically_significant is quantities["practically_significant"].value
    assert context.ledger.statistical_assessments == [bound]
