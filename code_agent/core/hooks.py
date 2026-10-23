"""AgentHook protocol + HookDecision enum.

Extracted from ``core/agent.py`` so the main loop file is decoupled from the
extension protocol. Hooks implement ``AgentHook``; the Agent main loop
consumes it. Both live in ``core`` (same layer), but in separate files so
refactoring the protocol does not require editing the main loop.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable

from code_agent.core.session import Session
from code_agent.tools.result import ToolResult


class HookDecision(Enum):
    """Decision returned by on_tool_call hooks."""

    ALLOW = "allow"  # Allow execution
    DENY = "deny"  # Deny execution
    MODIFY = "modify"  # Modify arguments then execute


@runtime_checkable
class AgentHook(Protocol):
    """Agent lifecycle hook protocol.

    Phase 1 defines the interface; Phase 2-4 implement concrete hooks.
    All methods have default no-op behavior — subclasses override only what they need.
    Agent iterates hooks list in registration order.

    Exception handling contract (two-layer defense):
    - Layer 1 (Hook implementer): on_session_start/on_turn_start/on_turn_end/on_tool_result
      should catch + log.warning + return None (degrade gracefully).
      on_tool_call should catch + log.error + return DENY (fail-safe).
    - Layer 2 (Agent defensive): Agent wraps hook calls in try/except as last resort.
    """

    async def on_session_start(self, session: Session) -> None:
        """Called before a new user input is processed.

        Phase 2: MemoryHook injects cross-session memory here.
        """
        return None

    async def on_turn_start(
        self, session: Session, turn: int, tools: list[dict]
    ) -> None:
        """Called before each reasoning turn.

        Phase 2: CompactionHook checks if compaction needed.
        Phase 3: PlanModeHook advances state machine.
        """
        return None

    async def on_tool_call(self, tool_name: str, arguments: dict) -> HookDecision:
        """Called before each tool execution.

        Phase 4: PermissionHook makes allow/deny/ask decisions.
        Phase 3: PlanModeHook blocks write tools in plan mode.

        Returns:
            HookDecision.ALLOW (default), DENY, or MODIFY.
        """
        return HookDecision.ALLOW

    async def on_tool_result(
        self, tool_name: str, arguments: dict, result: ToolResult
    ) -> None:
        """Called after each tool execution.

        Phase 4: LifecycleHook dispatches PostToolUse.
        Phase 2: EditTrackerHook records edited files.
        """
        return None

    async def on_turn_end(self, session: Session, turn: int) -> None:
        """Called after each reasoning turn completes.

        Phase 2: PersistenceHook saves session to disk.
        Phase 3: PlanModeHook handles plan mode exit.
        """
        return None

    async def on_session_end(
        self, session: Session, reason: str = "completed",
    ) -> None:
        """Called exactly once when the session ends (via ``Agent.run`` finally).

        Phase 4: LifecycleHook dispatches SESSION_END here.

        Args:
            session: The session that just ended.
            reason: "completed" | "max_turns" | "error".
        """
        return None
