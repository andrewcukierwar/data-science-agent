"""Library-only analytical execution, persisted through the existing tool ledger."""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from fractions import Fraction

from analytics.primitives import (
    Table,
    binary_experiment,
    coverage,
    entity_aggregate,
    number,
    reconciliation,
)
from orchestration.ledger import AnalysisLedger
from schemas.analytical import (
    AnalyticalRecord,
    AnalyticalResult,
    BinaryExperimentRequest,
    ContrastRequest,
    CoverageRequest,
    EntityAggregateRequest,
    InputBinding,
    QuantityRef,
    RatioRequest,
    ReconciliationRequest,
    TableInput,
)
from schemas.computation import ComputationBinding, ComputedField
from schemas.run_state import ToolEvent, ToolEventStatus
from tools.results import MAX_SQL_RESULT_BYTES, result_json

AnalyticalRequest = (
    CoverageRequest
    | EntityAggregateRequest
    | ContrastRequest
    | RatioRequest
    | ReconciliationRequest
    | BinaryExperimentRequest
)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _event(ledger, event_id):
    events = [event for event in ledger.tool_events if event.id == event_id]
    if len(events) != 1 or events[0].status is not ToolEventStatus.SUCCEEDED:
        raise ValueError("requires exact successful retained tool_event_id")
    event = events[0]
    if not event.output or event.output.get("result_contract_version") != "1.0":
        raise ValueError("requires retained execution output v1.0")
    return event


def validate_analytical_record(ledger, event_id, visited=None):
    """Validate retained identity/dependencies without recomputing arithmetic."""

    visited = set() if visited is None else set(visited)
    if event_id in visited or len(visited) >= 32:
        raise ValueError("cyclic or too-deep analytical dependency")
    visited.add(event_id)
    event = _event(ledger, event_id)
    if event.tool_name != "run_analytical":
        raise ValueError("not an analytical execution")
    record = AnalyticalRecord.model_validate(event.output["record"])
    if record.tool_event_id != event.id or record.result_id != "analytical-" + digest(
        record.model_dump(mode="json", exclude={"result_id"})
    ):
        raise ValueError("analytical result identity mismatch")
    if not record.inputs:
        raise ValueError("analytical execution requires retained source dependencies")
    for binding in record.inputs:
        parent = _event(ledger, binding.tool_event_id)
        if digest(parent.output) != binding.output_digest:
            raise ValueError("retained analytical input changed")
        if parent.tool_name == "run_analytical":
            validate_analytical_record(ledger, parent.id, visited)
        elif parent.tool_name != "run_sql":
            raise ValueError("unsupported analytical input type")
    return record


def analytical_binding(record, fields: dict[str, str], result_index=0):
    """Map existing claim fields to computed values, without copying numbers."""

    if not 0 <= result_index < len(record.results):
        raise ValueError("unknown analytical result index")
    mappings = []
    for field, quantity in fields.items():
        if quantity not in record.results[result_index].quantities:
            raise ValueError("unknown analytical quantity")
        token = quantity.replace("~", "~0").replace("/", "~1")
        mappings.append(
            ComputedField(
                field=field, pointer=f"/results/{result_index}/quantities/{token}/value"
            )
        )
    return ComputationBinding(
        tool_event_id=record.tool_event_id, source="analytical_record", fields=mappings
    )


def _quantity_fraction(quantity):
    if quantity.exact is not None:
        return Fraction(int(quantity.exact.numerator), int(quantity.exact.denominator))
    if quantity.value is None or isinstance(quantity.value, bool):
        raise ValueError("undefined/non-numeric quantity")
    return Fraction(quantity.value)


def _compatible(left, right, *, same_period, same_measure):
    a, b = left.scope, right.scope
    if (
        a.population != b.population
        or a.grain != b.grain
        or a.dimensions != b.dimensions
    ):
        raise ValueError("incompatible population/grain/dimensions")
    for choice in ("date_policy", "include_zero_activity"):
        if a.semantics.get(choice) != b.semantics.get(choice):
            raise ValueError("incompatible population/date selection policy")
    if same_period and a.period != b.period:
        raise ValueError("incompatible periods")
    if (a.period.end - a.period.start) != (b.period.end - b.period.start):
        raise ValueError("incompatible period durations")
    if (a.window is None) != (b.window is None):
        raise ValueError("incompatible observation windows")
    if a.window is not None:
        if a.window.model_dump(exclude={"observed_until"}) != b.window.model_dump(
            exclude={"observed_until"}
        ):
            raise ValueError("incompatible observation windows")
        if a.window.maturity != "require_complete" and (
            a.window.observed_until - a.period.end
        ) != (b.window.observed_until - b.period.end):
            raise ValueError("incompatible observation availability")
    if same_measure and (a.semantics != b.semantics or left.method != right.method):
        raise ValueError("incompatible measure/aggregation semantics")


class AnalyticalExecutionService:
    """One native, bounded-input computation and one terminal ledger event.

    Not registered with the Agents SDK. Input acquisition remains the existing
    bounded SQL service; no hidden SQL, model calls, or source rewrites occur.
    """

    def __init__(self, ledger: AnalysisLedger):
        self.ledger = ledger

    def execute(self, request: AnalyticalRequest) -> AnalyticalRecord:
        request = type(request).model_validate(request.model_dump())
        started = datetime.now(UTC)
        event_id = "tool-analytical-" + uuid.uuid4().hex
        inputs = {}

        def retained(ref):
            event = _event(self.ledger, ref.tool_event_id)
            inputs[event.id] = InputBinding(
                tool_event_id=event.id, output_digest=digest(event.output)
            )
            return event

        def table(ref: TableInput):
            event = retained(ref)
            out = event.output
            if (
                event.tool_name != "run_sql"
                or out.get("truncated", True)
                or out.get("row_count_is_lower_bound", True)
            ):
                raise ValueError(
                    "analytical tables require complete, untruncated SQL rows"
                )
            columns, types, rows = out["columns"], out["column_types"], out["rows"]
            if (
                len(rows) != out.get("row_count")
                or len(rows) != out.get("retained_row_count")
                or len(rows) > 100_000
            ):
                raise ValueError("incomplete or oversized retained SQL rows")
            if (
                len(columns) != len(set(columns))
                or len(types) != len(columns)
                or any(len(row) != len(columns) for row in rows)
            ):
                raise ValueError("ambiguous or malformed SQL table")
            return Table(
                tuple(columns),
                tuple(types),
                tuple(dict(zip(columns, row, strict=True)) for row in rows),
            )

        def quantity(ref: QuantityRef):
            retained(ref)
            record = validate_analytical_record(self.ledger, ref.tool_event_id)
            try:
                result = record.results[ref.result_index]
                return result, result.quantities[ref.quantity]
            except (IndexError, KeyError) as exc:
                raise ValueError("unknown retained quantity") from exc

        arguments = request.model_dump(mode="json")
        try:
            # Native arithmetic consumes one existing Python computation slot;
            # acquiring source SQL rows is charged separately by the SQL service.
            self.ledger.reserve_budget("python_executions")
            if isinstance(request, CoverageRequest):
                results = coverage(request, table(request.source))
            elif isinstance(request, EntityAggregateRequest):
                results = entity_aggregate(
                    request,
                    table(request.entities),
                    table(request.events) if request.events else None,
                )
            elif isinstance(request, ReconciliationRequest):
                results = reconciliation(request, table(request.source))
            elif isinstance(request, BinaryExperimentRequest):
                results = binary_experiment(request, table(request.source))
            elif isinstance(request, ContrastRequest):
                a, qa = quantity(request.baseline)
                value = _quantity_fraction(qa)
                quantities = {"baseline": qa}
                target = a
                if request.comparison is not None:
                    b, qb = quantity(request.comparison)
                    _compatible(a, b, same_period=False, same_measure=True)
                    if (
                        qa.unit != qb.unit
                        or request.baseline.quantity != request.comparison.quantity
                    ):
                        raise ValueError("incompatible quantity units/roles")
                    target = b
                    delta = _quantity_fraction(qb) - value
                    value = (
                        delta
                        if request.comparison_type == "absolute_difference"
                        else (delta / value if value else None)
                    )
                    quantities["comparison"] = qb
                quantities["value"] = number(
                    value,
                    "relative_change_fraction"
                    if request.comparison_type == "relative_change"
                    else qa.unit,
                    request.numeric,
                    "zero baseline" if value is None else None,
                )
                warnings = list(a.warnings)
                if request.comparison is not None:
                    warnings.extend(b.warnings)
                if (
                    request.comparison_type == "relative_change"
                    and _quantity_fraction(qa) < 0
                ):
                    warnings.append(
                        "Relative change uses the signed negative baseline."
                    )
                results = [
                    AnalyticalResult(
                        scope=target.scope.model_copy(
                            update={
                                "semantics": {
                                    "operation": "contrast",
                                    "comparison_type": request.comparison_type,
                                    "quantity": request.baseline.quantity,
                                    "baseline_scope": a.scope.model_dump(mode="json"),
                                    "comparison_scope": target.scope.model_dump(
                                        mode="json"
                                    )
                                    if request.comparison is not None
                                    else None,
                                }
                            }
                        ),
                        method=request.comparison_type,
                        quantities=quantities,
                        warnings=tuple(dict.fromkeys(warnings)),
                        details={
                            "baseline_period": a.scope.period.model_dump(mode="json")
                        },
                    )
                ]
            elif isinstance(request, RatioRequest):
                a, qa = quantity(request.numerator)
                b, qb = quantity(request.denominator)
                _compatible(a, b, same_period=True, same_measure=False)
                for ref, result in ((request.numerator, a), (request.denominator, b)):
                    if not (
                        ref.quantity in {"numerator_sum", "denominator_sum"}
                        or (
                            ref.quantity == "value"
                            and result.scope.semantics.get("aggregation") == "sum"
                        )
                    ):
                        raise ValueError(
                            "ratio requires aggregate totals, not means/ratios"
                        )
                denominator = _quantity_fraction(qb)
                if not denominator and request.zero_denominator == "error":
                    raise ValueError("zero denominator")
                value = _quantity_fraction(qa) / denominator if denominator else None
                results = [
                    AnalyticalResult(
                        scope=a.scope.model_copy(
                            update={
                                "semantics": {
                                    "operation": "ratio_of_totals",
                                    "semantic_key": request.semantic_key,
                                    "numerator": a.scope.semantics,
                                    "denominator": b.scope.semantics,
                                    "numerator_quantity": request.numerator.quantity,
                                    "denominator_quantity": (
                                        request.denominator.quantity
                                    ),
                                    "numerator_unit": qa.unit,
                                    "denominator_unit": qb.unit,
                                    "zero_denominator": request.zero_denominator,
                                }
                            }
                        ),
                        method="ratio of independent aggregate totals",
                        quantities={
                            "numerator": qa,
                            "denominator": qb,
                            "value": number(
                                value,
                                request.unit,
                                request.numeric,
                                "zero denominator" if value is None else None,
                            ),
                        },
                        warnings=tuple(dict.fromkeys([*a.warnings, *b.warnings])),
                    )
                ]
            else:
                raise ValueError("unsupported analytical operation")
            record = AnalyticalRecord(
                tool_event_id=event_id,
                result_id="pending",
                operation=request.operation,
                request=arguments,
                inputs=tuple(inputs[k] for k in sorted(inputs)),
                results=tuple(results),
            )
            record = record.model_copy(
                update={
                    "result_id": "analytical-"
                    + digest(record.model_dump(mode="json", exclude={"result_id"}))
                }
            )
            output = {
                "result_contract_version": "1.0",
                "record": record.model_dump(mode="json"),
            }
            if len(result_json(output)) > MAX_SQL_RESULT_BYTES:
                raise ValueError("analytical result exceeds retained byte limit")
        except Exception as exc:
            self.ledger.append_tool_event(
                ToolEvent(
                    id=event_id,
                    tool_name="run_analytical",
                    status=ToolEventStatus.FAILED,
                    started_at=started,
                    completed_at=datetime.now(UTC),
                    arguments=arguments,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            raise
        self.ledger.append_tool_event(
            ToolEvent(
                id=event_id,
                tool_name="run_analytical",
                status=ToolEventStatus.SUCCEEDED,
                started_at=started,
                completed_at=datetime.now(UTC),
                arguments=arguments,
                output=output,
            )
        )
        return record
