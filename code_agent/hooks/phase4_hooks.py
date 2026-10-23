# [Phase 4] 2026-07-29: PermissionHook + LifecycleHook — Phase 4 AgentHook implementations
# [2026-08-04] PermissionHook: ASK is now fail-closed (deny by default) — aligns
# with Rust PromptPolicy::Deny. Use ask_action="prompt" for interactive REPL.
"""Phase 4 AgentHook implementations: PermissionHook, LifecycleHook.

References:
    - design-docs/Python-Agent-MVP-Phase4-详细设计.md §10 Phase 4 hooks assembly

PermissionHook: adapts PermissionManager → AgentHook.on_tool_call
    - Decision.ALLOW → HookDecision.ALLOW
    - Decision.DENY → HookDecision.DENY
    - Decision.ASK →
        ask_action="deny"    (default) → HookDecision.DENY  (fail-closed)
        ask_action="prompt"           → interactive console confirmation
        ask_action="allow"            → HookDecision.ALLOW (legacy compat)

LifecycleHook: adapts HookDispatcher → AgentHook (all lifecycle methods)
    - Delegates on_session_start/turn_start/tool_call/tool_result/turn_end
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from code_agent.core.hooks import HookDecision
from code_agent.hooks.dispatcher import HookEvent
from code_agent.permission.manager import Decision

if TYPE_CHECKING:
    from code_agent.core.session import Session
    from code_agent.hooks.dispatcher import HookDispatcher
    from code_agent.permission.manager import PermissionManager
    from code_agent.tools.result import ToolResult

logger = logging.getLogger(__name__)


# ── PermissionHook ──────────────────────────────────────────────────────────


class PermissionHook:
    """Adapts PermissionManager to the AgentHook interface.

    Decision mapping:
        ALLOW → HookDecision.ALLOW
        DENY  → HookDecision.DENY
        ASK   → depends on ask_action (deny / prompt / allow)

    ``ask_action`` mirrors Rust's PromptPolicy:
        - "deny"    (default): fail-closed — non-pre-approved tools are DENIED.
        - "prompt": interactive console confirmation (REPL mode).
        - "allow":  legacy allow-by-default (opt-in for compatibility).

    Usage::

        pm = PermissionManager(yolo_mode=False, allow_rules=["Bash(npm*)"])
        hook = PermissionHook(pm)                 # headless → deny
        hook = PermissionHook(pm, ask_action="prompt")  # interactive REPL
        # In _build_agent():
        hooks.append(hook)
    """

    def __init__(
        self,
        permission_manager: "PermissionManager",
        *,
        ask_action: str = "deny",
    ):
        self._pm = permission_manager
        self._ask_action = ask_action
        self.Decision = Decision  # for local reference

    async def on_tool_call(
        self, tool_name: str, arguments: dict,
    ) -> HookDecision:
        """Permission check before every tool execution.

        Args:
            tool_name: Tool name.
            arguments: Tool arguments.

        Returns:
            HookDecision.ALLOW or DENY.
        """
        try:
            decision = await self._pm.decide(tool_name, arguments)

            if decision == self.Decision.ALLOW:
                return HookDecision.ALLOW
            elif decision == self.Decision.DENY:
                logger.info(
                    "Permission denied: %s %s",
                    tool_name,
                    arguments.get("command") or arguments.get("file_path") or "",
                )
                return HookDecision.DENY
            elif decision == self.Decision.ASK:
                if self._ask_action == "prompt":
                    return await self._prompt_user(tool_name, arguments)
                if self._ask_action == "allow":
                    return HookDecision.ALLOW  # legacy opt-in
                # Default: fail-closed (Rust PromptPolicy::Deny)
                logger.info(
                    "Permission denied by prompt policy (tool not "
                    "pre-approved): %s",
                    tool_name,
                )
                return HookDecision.DENY
        except Exception:
            logger.error(
                "PermissionHook.on_tool_call exception — treating as DENY",
                exc_info=True,
            )
            return HookDecision.DENY

        return HookDecision.ALLOW

    async def _prompt_user(
        self, tool_name: str, arguments: dict,
    ) -> HookDecision:
        """Interactive console confirmation (REPL mode).

        Runs in a worker thread so the asyncio loop is not blocked.
        Any failure to obtain a decision (EOF/Ctrl+C/exception) → DENY.
        """
        summary = _summarize_tool_call(tool_name, arguments)
        try:
            import asyncio

            answer = await asyncio.to_thread(
                input, f"Allow {summary}? [y/N] ",
            )
        except (EOFError, KeyboardInterrupt, Exception):
            return HookDecision.DENY

        return (
            HookDecision.ALLOW
            if answer.strip().lower() in ("y", "yes")
            else HookDecision.DENY
        )


# ── LifecycleHook ───────────────────────────────────────────────────────────


class LifecycleHook:
    """Wraps Phase 4 HookDispatcher as an AgentHook.

    Delegates Agent lifecycle events to user-registered handlers
    in the HookDispatcher. Handlers can observe or modify all phases.

    Usage::

        dispatcher = HookDispatcher()
        dispatcher.register(HookEvent.PRETOOL_USE, my_handler)
        hook = LifecycleHook(dispatcher)
        hooks.append(hook)
    """

    def __init__(self, dispatcher: "HookDispatcher"):
        self._dispatcher = dispatcher

    async def on_session_start(self, session: "Session") -> None:
        """Dispatch SESSION_START event."""
        try:
            await self._dispatcher.dispatch(HookEvent.SESSION_START, {
                "session": session,
            })
        except Exception:
            logger.warning("LifecycleHook.on_session_start failed", exc_info=True)

    async def on_turn_start(
        self, session: "Session", turn: int, tools: list[dict],
    ) -> None:
        """Dispatch TURN_START event."""
        try:
            await self._dispatcher.dispatch(HookEvent.TURN_START, {
                "session": session,
                "turn": turn,
                "tools": tools,
            })
        except Exception:
            logger.warning("LifecycleHook.on_turn_start failed", exc_info=True)

    async def on_tool_call(
        self, tool_name: str, arguments: dict,
    ) -> HookDecision:
        """Dispatch PreToolUse event. First deny stops the chain."""
        try:
            result = await self._dispatcher.dispatch(HookEvent.PRETOOL_USE, {
                "tool_name": tool_name,
                "arguments": arguments,
            })

            if result.action == "deny":
                return HookDecision.DENY
            if result.action == "modify" and result.modified_args:
                # Modify arguments in-place
                arguments.clear()
                arguments.update(result.modified_args)
                return HookDecision.MODIFY
        except Exception:
            logger.warning("LifecycleHook.on_tool_call failed", exc_info=True)

        return HookDecision.ALLOW

    async def on_tool_result(
        self, tool_name: str, arguments: dict, result: "ToolResult",
    ) -> None:
        """Dispatch PostToolUse or PostToolUseFailure event."""
        try:
            if result.is_error:
                await self._dispatcher.dispatch(
                    HookEvent.POST_TOOL_USE_FAILURE, {
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "result": result,
                    },
                )
            else:
                await self._dispatcher.dispatch(HookEvent.POST_TOOL_USE, {
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "result": result,
                })
        except Exception:
            logger.warning("LifecycleHook.on_tool_result failed", exc_info=True)

    async def on_turn_end(self, session: "Session", turn: int) -> None:
        """Dispatch TURN_END event."""
        try:
            await self._dispatcher.dispatch(HookEvent.TURN_END, {
                "session": session,
                "turn": turn,
            })
        except Exception:
            logger.warning("LifecycleHook.on_turn_end failed", exc_info=True)

    async def on_session_end(
        self, session: "Session", reason: str = "completed",
    ) -> None:
        """Dispatch SESSION_END event (fired once by Agent.run finally)."""
        try:
            await self._dispatcher.dispatch(HookEvent.SESSION_END, {
                "session": session,
                "reason": reason,
            })
        except Exception:
            logger.warning("LifecycleHook.on_session_end failed", exc_info=True)


# ── TelemetryHook ────────────────────────────────────────────────────────────


class TelemetryHook:
    """Wires the TelemetryLogger into the Agent lifecycle.

    Emits session_create / turn_start / turn_complete events. Register it
    AFTER PersistenceHook so ``session._session_id`` exists by the time the
    logger is bound.

    Exception handling: telemetry failures are logged and never crash the
    Agent (privacy/observability is best-effort).
    """

    def __init__(self, telemetry_logger):
        self._logger = telemetry_logger

    async def on_session_start(self, session: "Session") -> None:
        try:
            sid = getattr(session, "_session_id", None) or "unknown"
            self._logger.set_session(sid)
            self._logger.log("session_create")
        except Exception:
            logger.warning("TelemetryHook.on_session_start failed", exc_info=True)

    async def on_turn_start(
        self, session: "Session", turn: int, tools: list[dict],
    ) -> None:
        try:
            self._logger.log("turn_start", turn=turn, tool_count=len(tools))
        except Exception:
            logger.warning("TelemetryHook.on_turn_start failed", exc_info=True)

    async def on_turn_end(self, session: "Session", turn: int) -> None:
        try:
            self._logger.log(
                "turn_complete",
                turn=turn,
                estimated_tokens=session.estimated_tokens,
                prompt_count=session.prompt_count,
            )
        except Exception:
            logger.warning("TelemetryHook.on_turn_end failed", exc_info=True)


# ── Internal helpers ─────────────────────────────────────────────────────────


def _summarize_tool_call(tool_name: str, arguments: dict, max_len: int = 80) -> str:
    """Build a one-line human-readable summary of a tool call for prompting.

    Args:
        tool_name: Tool name.
        arguments: Tool arguments dict.
        max_len: Maximum length of the argument summary.

    Returns:
        e.g. 'file_edit(file_path="src/main.py")'.
    """
    args = dict(arguments or {})
    # Prefer the most decision-relevant argument for display.
    for key in ("command", "file_path", "path", "url"):
        if key in args:
            val = str(args[key])
            if len(val) > max_len:
                val = val[:max_len] + "..."
            return f'{tool_name}({key}="{val}")'
    if args:
        items = " ".join(f"{k}=..." for k in sorted(args)[:4])
        return f"{tool_name}({items})"
    return f"{tool_name}()"
