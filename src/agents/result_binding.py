"""Bind numerical claims to persisted execution fields without model arithmetic."""

from __future__ import annotations

import json
import math
import re
from hashlib import sha256
from typing import TYPE_CHECKING

from agents.evidence import (
    EvidenceProvenanceError,
    executed_references,
    finding_reference_aliases,
    has_source_lineage,
    resolve_citations,
)
from schemas.findings import Finding
from schemas.metrics import MetricComparison, normalize_metric_comparison
from schemas.run_state import ToolEventStatus
from schemas.statistics import StatisticalAssessment

if TYPE_CHECKING:
    from orchestration.ledger import AnalysisLedger


class ResultBindingError(EvidenceProvenanceError):
    """A numerical claim is missing or contradicts its computed source."""


def _claim_label(claim) -> str:
    return claim.id if isinstance(claim, Finding) else claim.metric_key


def _pointer(document: object, pointer: str) -> object:
    current = document
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list) and token.isdecimal():
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            raise ValueError(f"unresolvable computed output pointer: {pointer}")
    return current


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def resolve_result[Claim: MetricComparison | StatisticalAssessment | Finding](
    claim: Claim,
    ledger: AnalysisLedger,
    *,
    require_bound: bool | None = None,
) -> Claim:
    """Hydrate omitted fields, reject mismatches, and return a stable result ID.

    No tolerance, conversion, unit rescaling, stdout regex, file lookup, alias
    guessing, or model-authored numerical fallback is used on the bound path.
    """

    required = (
        ledger.state.schema_version in {"1.2", "1.3"}
        if require_bound is None
        else require_bound
    )
    binding = claim.computation
    if binding is None:
        if required or claim.result_id is not None:
            raise ResultBindingError(f"unbound numerical result: {_claim_label(claim)}")
        # Explicit legacy path: still check statistical citations and lineage.
        if isinstance(claim, StatisticalAssessment):
            _validate_provenance(claim, ledger)
        return claim
    try:
        events = [
            event for event in ledger.tool_events if event.id == binding.tool_event_id
        ]
        if len(events) != 1:
            raise ValueError("binding requires an exact, unique tool_event_id")
        event = events[0]
        output = event.output or {}
        if (
            event.status is not ToolEventStatus.SUCCEEDED
            or output.get("result_contract_version") != "1.0"
        ):
            raise ValueError(
                "binding requires successful retained execution output v1.0"
            )
        if binding.source == "sql_rows" and event.tool_name == "run_sql":
            document = output["rows"]
        elif binding.source == "python_stdout_json" and event.tool_name == "run_python":
            if output.get("stdout_truncated", False):
                raise ValueError("cannot bind truncated Python stdout")
            document = json.loads(output["stdout"], object_pairs_hook=_unique_object)
        elif (
            binding.source == "analytical_record"
            and event.tool_name == "run_analytical"
        ):
            from tools.analytical import validate_analytical_record

            document = validate_analytical_record(ledger, event.id).model_dump(
                mode="json"
            )
            # Only published quantity values are bindable, never exact-number
            # strings, metadata, or request literals such as a threshold.
            if any(
                not re.fullmatch(r"/results/\d+/quantities/[^/]+/value", f.pointer)
                for f in binding.fields
            ):
                raise ValueError(
                    "analytical binding must address a published quantity value"
                )
        else:
            raise ValueError("binding source does not match execution type")
        fields = [item.field for item in binding.fields]
        if len(set(fields)) != len(fields) or set(fields) != set(
            claim.numerical_fields
        ):
            raise ValueError("binding must supply every numerical field exactly once")
        if not has_source_lineage(ledger, [event.id]):
            raise ValueError("computed execution lacks source lineage")
        data = claim.model_dump(mode="json")
        for field in binding.fields:
            value = _pointer(document, field.pointer)
            is_boolean = field.field == "practically_significant"
            if is_boolean:
                valid = type(value) is bool
            else:
                valid = type(value) in (int, float) and math.isfinite(value)
            if not valid:
                raise ValueError(
                    f"computed field {field.field} is not a finite typed number/boolean"
                )
            parts = field.field.split(".")
            parent = data
            for part in parts[:-1]:
                if parent.get(part) is None:
                    parent[part] = {}
                parent = parent[part]
            supplied = parent.get(parts[-1])
            if supplied is not None and (
                supplied != value or isinstance(supplied, bool) != is_boolean
            ):
                raise ValueError(
                    f"{field.field} contradicts computed output: "
                    f"{supplied!r} != {value!r}"
                )
            parent[parts[-1]] = value
        data["evidence_refs"] = list(dict.fromkeys([*claim.evidence_refs, event.id]))
        resolved = type(claim).model_validate(data)
        if isinstance(resolved, MetricComparison):
            resolved = normalize_metric_comparison(resolved)
        for field in binding.fields:
            actual = resolved.model_dump(mode="json")
            for part in field.field.split("."):
                actual = actual[part]
            if actual != _pointer(document, field.pointer):
                raise ValueError(f"{field.field} cannot be represented exactly")
        _validate_provenance(resolved, ledger)
        identity = resolved.model_dump(
            mode="json", exclude={"result_id", "evidence_refs"}
        )
        digest = sha256(
            json.dumps(identity, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        result_id = f"computed-{digest}"
        if claim.result_id is not None and claim.result_id != result_id:
            raise ValueError("result_id does not match the bound result")
        return resolved.model_copy(update={"result_id": result_id})
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ResultBindingError(f"{_claim_label(claim)}: {error}") from error


def _validate_provenance(claim, ledger: AnalysisLedger) -> None:
    resolution = resolve_citations(
        claim.evidence_refs,
        executed_refs=executed_references(ledger),
        aliases=finding_reference_aliases(ledger),
    )
    if not resolution.is_supported or not has_source_lineage(
        ledger, claim.evidence_refs
    ):
        raise ResultBindingError(
            f"{_claim_label(claim)}: numerical result lacks executed source provenance"
        )


def resolve_outputs(result, ledger: AnalysisLedger):
    """One numerical contract shared by both specialists and both finalizers."""

    return result.model_copy(
        update={
            "findings": [
                resolve_result(item, ledger)
                if item.value is not None
                or item.computation is not None
                or item.result_id is not None
                else item
                for item in result.findings
            ],
            "metric_comparisons": [
                resolve_result(item, ledger) for item in result.metric_comparisons
            ],
            "statistical_assessments": [
                resolve_result(item, ledger) for item in result.statistical_assessments
            ],
        }
    )


def select_results(result, ledger: AnalysisLedger):
    """Explicit selection preserves whole records, including identity and caveats."""

    available = [*ledger.metric_comparisons, *ledger.statistical_assessments]
    for record in ledger.specialist_results:
        available.extend(record.result.metric_comparisons)
        available.extend(record.result.statistical_assessments)
    metrics = list(result.metric_comparisons)
    statistics = list(result.statistical_assessments)
    caveats = list(result.caveats)
    for result_id in result.selected_result_ids:
        matches = [item for item in available if item.result_id == result_id]
        if not matches:
            raise ResultBindingError(f"unknown selected result_id: {result_id}")
        selected = resolve_result(matches[0], ledger, require_bound=True)
        target = metrics if isinstance(selected, MetricComparison) else statistics
        if selected not in target:
            target.append(selected)
        for record in ledger.specialist_results:
            if selected in [
                *record.result.metric_comparisons,
                *record.result.statistical_assessments,
            ]:
                caveats.extend(record.result.caveats)
    return result.model_copy(
        update={
            "metric_comparisons": metrics,
            "statistical_assessments": statistics,
            "caveats": list(dict.fromkeys(caveats)),
        }
    )


def validate_statistical_selection(assessments: list[StatisticalAssessment]) -> None:
    """Historical records are not candidates; unresolved selected disagreements fail."""

    from schemas.metrics import (
        normalize_metric_dimensions,
        normalize_metric_key,
        normalize_metric_period,
    )

    seen = {}
    for item in assessments:
        identity = (
            normalize_metric_key(item.metric_key, item.dimensions),
            tuple(
                (d.name, d.value) for d in normalize_metric_dimensions(item.dimensions)
            ),
            normalize_metric_period(item.baseline_period),
            normalize_metric_period(item.comparison_period),
        )
        payload = tuple(
            getattr(item, field.split(".")[0]) for field in item.numerical_fields
        ) + (item.conclusion, item.causal_interpretation)
        if identity in seen and seen[identity] != payload:
            raise ResultBindingError(
                f"conflicting selected statistical assessments: {item.metric_key}"
            )
        seen[identity] = payload


def validate_metric_selection(comparisons: list[MetricComparison]) -> None:
    """Two selected computations for one estimand cannot silently overwrite."""

    from schemas.metrics import (
        metric_comparison_scope_identity,
        metric_definition_contexts_compatible,
    )

    for index, left in enumerate(comparisons):
        for right in comparisons[index + 1 :]:
            if (
                left.computation is not None
                and right.computation is not None
                and metric_comparison_scope_identity(left)
                == metric_comparison_scope_identity(right)
                and metric_definition_contexts_compatible(
                    left.definition_context, right.definition_context
                )
                and left.value != right.value
            ):
                raise ResultBindingError(
                    f"conflicting selected metric comparisons: {left.metric_key}"
                )
