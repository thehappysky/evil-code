# [Phase 4] 2026-07-29: HookDispatcher — lifecycle event dispatch engine
"""Lifecycle event dispatcher.

References:
    - design-docs/Python-Agent-MVP-Phase4-详细设计.md §5 hooks/dispatcher.py
    - Rust xai-agent-lifecycle + xai-grok-hooks

Design:
    - Handlers registered per-event type
    - Multiple handlers per event (called in registration order)
    - PreToolUse: first "deny" result stops chain and prevents execution
    - PostToolUse/PostToolUseFailure: all handlers run (best-effort, errors logged)
    - SessionStart/SessionEnd/TurnStart/TurnEnd: all handlers run
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum
from typing import Any, Literal

logger = logging.getLogger(__name__)

# ── Types ───────────────────────────────────────────────────────────────────


class HookEvent(Enum):
    """Hook lifecycle event type."""

    PRETOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    POST_TOOL_USE_FAILURE = "post_tool_use_failure"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    TURN_START = "turn_start"
    TURN_END = "turn_end"


class HookResult:
    """Result from a hook handler.

    Attributes:
        action: "allow", "deny", or "modify".
        message: Optional message for the model.
        modified_args: Modified tool arguments (for modify action).
    """

    def __init__(
        self,
        action: Literal["allow", "deny", "modify"] = "allow",
        message: str | None = None,
        modified_args: dict | None = None,
    ):
        self.action = action
        self.message = message
        self.modified_args = modified_args


# Handler type: async function(event, context) → HookResult
HookHandler = Callable[..., Any]


# ── HookDispatcher ──────────────────────────────────────────────────────────


class HookDispatcher:
    """Lifecycle event dispatcher.

    Handlers are registered per-event type and called in registration order.

    Usage::

        dispatcher = HookDispatcher()
        dispatcher.register(HookEvent.PRETOOL_USE, my_handler)
        result = await dispatcher.dispatch(HookEvent.PRETOOL_USE, context)
    """

    def __init__(self):
        self._handlers: dict[HookEvent, list[HookHandler]] = {
            event: [] for event in HookEvent
        }

    def register(self, event: HookEvent, handler: HookHandler) -> None:
        """Register a hook handler for a specific event.

        Handlers are called in registration order.

        Args:
            event: The lifecycle event type.
            handler: Async function(event, context) → HookResult.
        """
        self._handlers[event].append(handler)
        logger.debug(
            "Registered handler for %s (total: %d)",
            event.value, len(self._handlers[event]),
        )

    async def dispatch(
        self,
        event: HookEvent,
        context: dict[str, Any],
    ) -> HookResult:
        """Dispatch event to all registered handlers.

        For PreToolUse: returns "deny" if ANY handler blocks execution.
        For non-blocking events: always returns "allow".

        Handler exceptions are caught and logged (never crash the agent).

        Args:
            event: The lifecycle event to dispatch.
            context: Event context dict (tool_name, arguments, session, etc.).

        Returns:
            Aggregated HookResult from all handlers.
        """
        handlers = self._handlers.get(event, [])

        if not handlers:
            return HookResult(action="allow")

        final_result = HookResult(action="allow")

        for handler in handlers:
            try:
                result = await handler(event, context)

                # Normalize result to HookResult
                if not isinstance(result, HookResult):
                    result = HookResult(action="allow")

                # PreToolUse: first deny wins
                if event == HookEvent.PRETOOL_USE:
                    if result.action == "deny":
                        logger.info(
                            "PreToolUse denied by handler: %s",
                            getattr(handler, "__name__", "unknown"),
                        )
                        return result
                    if result.action == "modify":
                        final_result = result

                # Post-events: collect messages
                if result.message:
                    if final_result.message:
                        final_result.message += "\n" + result.message
                    else:
                        final_result.message = result.message

            except Exception:
                logger.warning(
                    "Hook handler %s failed for event %s",
                    getattr(handler, "__name__", "unknown"),
                    event.value,
                    exc_info=True,
                )
                # Continue with remaining handlers

        return final_result
