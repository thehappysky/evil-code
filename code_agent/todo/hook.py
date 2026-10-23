# [Phase 5] 2026-08-04: TodoHook — injects todo reminders + nudges the model
"""TodoHook — AgentHook that keeps the todo list in front of the model.

References:
    - design-docs/Python-Agent-补足方案-设计.md §Phase 5
    - Rust todo reminder injection + nudge (xai-grok-tools)

Behavior:
    - on_turn_start: if there are pending todos, inject them as a
      ``<system-reminder>`` synthetic user message so the model stays on task
      without relying on its own memory.
    - on_tool_result: reset the nudge counter when ``todo_write`` succeeds.
    - Nudge: if the model has written todos but then ignored them for several
      turns (no todo_write), inject a gentle reminder that the tool exists.

The turn-end ``TodoGate`` (forcing another turn when the model stops
mid-list) is intentionally not implemented yet — injecting a reminder is the
safe, minimal first step.
"""

from __future__ import annotations

import logging

from code_agent.prompts.reminders import ReminderInjector
from code_agent.core.session import Session
from code_agent.tools.result import ToolResult

logger = logging.getLogger(__name__)

_NUDGE_AFTER_TURNS = 4
_NUDGE_REMINDER = (
    "<system-reminder>\n"
    "You have an active todo list but have not updated it recently. "
    "Use the todo_write tool to track your progress.\n"
    "</system-reminder>"
)


class TodoHook:
    """Adapts TodoController to the AgentHook lifecycle."""

    def __init__(self, controller):
        self._controller = controller
        self._injector = ReminderInjector()
        self._turns_since_todo_write = 0
        self._nudged: bool = False

    async def on_turn_start(
        self, session: Session, turn: int, tools: list[dict],
    ) -> None:
        try:
            items = self._controller.snapshot()
            pending = [t for t in items if t.status.value in ("pending", "in_progress")]
            if pending:
                reminder = self._injector.todo_reminder(
                    [
                        {"subject": t.content, "status": t.status.value}
                        for t in pending[:10]
                    ],
                )
                if reminder:
                    session.add_user_message(reminder)

            # Nudge: has a todo list, but the model stopped touching it.
            self._turns_since_todo_write += 1
            if (
                items
                and not self._nudged
                and self._turns_since_todo_write >= _NUDGE_AFTER_TURNS
            ):
                session.add_user_message(_NUDGE_REMINDER)
                self._nudged = True
        except Exception:
            logger.warning("TodoHook.on_turn_start failed", exc_info=True)

    async def on_tool_result(
        self, tool_name: str, arguments: dict, result: ToolResult,
    ) -> None:
        try:
            if tool_name == "todo_write" and not result.is_error:
                self._turns_since_todo_write = 0
                self._nudged = False
        except Exception:
            logger.warning("TodoHook.on_tool_result failed", exc_info=True)
