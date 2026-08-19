"""Response-boundary model usage accounting shared by every agent run.

The retained Task 10 pilots recorded usage only after ``Runner.run()``
returned, so an invalid-JSON final output threw away every token the provider
had already reported and billed. Usage is now recorded as each response
arrives, and whatever the run accumulated is reconciled once when the run ends
— successfully or not.

Two mechanisms cooperate so neither can lose or double-count a response:

* ``ModelUsageHooks.on_llm_end`` records each response as it arrives, which is
  the only point that survives an exception raised deeper in the same turn;
* ``reconcile`` compares what was recorded against the run's authoritative
  cumulative usage and records only the remainder, which covers responses whose
  hook never fired because the turn failed first.

When neither mechanism can produce an authoritative total, usage is explicitly
marked incomplete so cost is published as unavailable instead of as a confident
total that omits real calls.
"""

from __future__ import annotations

from typing import Any

from agents import RunHooks, Runner
from agents.runtime import AgentRunContext
from schemas.run_state import (
    ModelUsage,
    add_model_usage,
    model_usage_snapshot,
    subtract_model_usage,
)


class ModelUsageRecorder:
    """Record one agent run's provider usage exactly once."""

    def __init__(self, context: AgentRunContext) -> None:
        self._context = context
        self._recorded = ModelUsage()

    @property
    def recorded(self) -> ModelUsage:
        """Usage this recorder has already persisted for the current run."""

        return self._recorded

    def record_response(self, usage: object) -> ModelUsage:
        """Persist one provider response at its boundary."""

        delta = model_usage_snapshot(usage)
        if delta == ModelUsage():
            return self._recorded
        self._record(delta)
        return self._recorded

    def reconcile(self, cumulative: object, *, source: str) -> None:
        """Record whatever the run reported but the response hooks did not see.

        ``cumulative`` is the run's total usage, so only the part beyond what
        was already recorded is persisted. A missing total means the run's
        usage cannot be proven complete.
        """

        if cumulative is None:
            self._context.ledger.mark_usage_incomplete(
                f"{source} ended without a reconcilable usage total, so recorded "
                "usage is a lower bound."
            )
            return
        remainder = subtract_model_usage(
            model_usage_snapshot(cumulative),
            self._recorded,
        )
        if remainder != ModelUsage():
            self._record(remainder)

    def _record(self, delta: ModelUsage) -> None:
        self._context.ledger.record_usage_delta(delta)
        self._recorded = add_model_usage(self._recorded, delta)


class ModelUsageHooks(RunHooks[AgentRunContext]):
    """Persist provider usage at each response boundary of a run."""

    async def on_llm_end(
        self,
        context: Any,
        agent: Any,
        response: Any,
    ) -> None:
        recorder = _active_recorder(context)
        if recorder is not None:
            recorder.record_response(getattr(response, "usage", None))


def _active_recorder(context: Any) -> ModelUsageRecorder | None:
    """Resolve the recorder for the agent run that owns this hook call."""

    run_context = getattr(context, "context", None)
    return getattr(run_context, "usage_recorder", None)


def _cumulative_usage(source: object) -> object | None:
    """Return a run's authoritative cumulative usage, if it exposes one."""

    wrapper = getattr(source, "context_wrapper", None)
    if wrapper is None:
        run_data = getattr(source, "run_data", None)
        wrapper = getattr(run_data, "context_wrapper", None)
    return getattr(wrapper, "usage", None)


async def run_agent_with_usage(
    agent: Any,
    agent_input: Any,
    *,
    context: AgentRunContext,
    max_turns: int,
    hooks: RunHooks[AgentRunContext] | None = None,
) -> Any:
    """Run an agent so its usage survives every failure path.

    Nested specialist runs share the parent run's usage accumulator, so the
    parent reconciliation also covers specialist responses whose own hooks did
    not fire.
    """

    recorder = ModelUsageRecorder(context)
    previous = context.usage_recorder
    context.usage_recorder = recorder
    try:
        try:
            result = await Runner.run(
                agent,
                agent_input,
                context=context,
                max_turns=max_turns,
                hooks=hooks or ModelUsageHooks(),
            )
        except Exception as error:
            recorder.reconcile(
                _cumulative_usage(error),
                source=f"{getattr(agent, 'name', 'agent')} run",
            )
            raise
        recorder.reconcile(
            _cumulative_usage(result),
            source=f"{getattr(agent, 'name', 'agent')} run",
        )
        return result
    finally:
        context.usage_recorder = previous


__all__ = [
    "ModelUsageHooks",
    "ModelUsageRecorder",
    "run_agent_with_usage",
]
