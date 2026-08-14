"""Interfaces used by tools to append observable events to a run ledger."""

from typing import Protocol

from schemas.run_state import ToolEvent


class ToolEventLedger(Protocol):
    """Minimal ledger boundary required by tool execution services."""

    def append_tool_event(self, event: ToolEvent) -> None:
        """Persist one structured tool event."""


ToolEventSink = ToolEventLedger
