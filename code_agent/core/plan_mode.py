# [Phase 3] 2026-07-27: Plan Mode — finite state machine for plan-first-then-code workflow
"""Plan Mode finite state machine.

References:
    - design-docs/Python-Agent-MVP-Phase3-详细设计.md §8 core/plan_mode.py
    - Rust PlanModeState (plan_mode.rs:17-43)

State transitions:

    INACTIVE ──→ PENDING        (user runs /plan command)
    PENDING  ──→ ACTIVE         (next prompt arrives, plan instructions injected)
    PENDING  ──→ INACTIVE       (user cancels before prompt)
    ACTIVE   ──→ EXIT_PENDING   (user runs /plan while turn in flight)
    EXIT_PENDING → INACTIVE     (turn completes, exit reminder injected)
    ACTIVE   ──→ INACTIVE       (ExitPlanMode tool called + user approved)

While ACTIVE:
    - Write tools are blocked: file_edit, file_write, shell_exec writes
    - Read tools remain available: file_read, file_search
    - Plan-mode tools available: enter_plan_mode, exit_plan_mode
    - System prompt includes plan mode instructions
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Tools allowed during Plan Mode (read-only)
_READ_TOOLS = frozenset({
    "file_read",
    "file_search",
    "web_fetch",
})

# Tools blocked during Plan Mode (write tools)
_WRITE_TOOLS = frozenset({
    "file_edit",
    "file_write",
    "file_delete",
    "shell_exec",
})

# Plan mode system instructions injected when ACTIVE
# (centralized in code_agent.prompts, adapted from grok-build EnterPlanMode).
from code_agent.prompts import (
    PLAN_MODE_EXIT_REMINDER as _EXIT_REMINDER,
    PLAN_MODE_INSTRUCTIONS as _PLAN_MODE_INSTRUCTIONS,
)


# ── PlanState Enum ────────────────────────────────────────────────────────────


class PlanState(Enum):
    """Plan Mode state enumeration."""

    INACTIVE = "inactive"
    PENDING = "pending"            # User toggled ON, waiting for prompt
    ACTIVE = "active"              # Plan instructions active, write tools blocked
    EXIT_PENDING = "exit_pending"  # User toggled OFF, waiting for turn completion


# ── PlanModeTracker ───────────────────────────────────────────────────────────


class PlanModeTracker:
    """Plan Mode finite state machine.

    Tracks the lifecycle of Plan Mode and provides tool-gating decisions.

    Usage::

        tracker = PlanModeTracker(plan_file_path=Path("plan.md"))
        tracker.activate()              # /plan command → PENDING
        instructions = tracker.on_prompt_arrived()  # → ACTIVE, returns instructions
        tracker.is_tool_allowed("file_edit")  # → False while ACTIVE
        tracker.deactivate()            # /plan again → EXIT_PENDING or INACTIVE
    """

    def __init__(self, plan_file_path: Path):
        self._state: PlanState = PlanState.INACTIVE
        self._plan_file_path = plan_file_path
        self._turn_in_flight: bool = False

    # ── State transitions ───────────────────────────────────────────────────

    def activate(self) -> None:
        """Transition to PENDING state.

        Called when user runs /plan.
        If already ACTIVE (or EXIT_PENDING), calls deactivate instead.
        """
        if self._state == PlanState.ACTIVE:
            self.deactivate()
            return

        if self._state == PlanState.EXIT_PENDING:
            self.deactivate()
            return

        if self._state == PlanState.INACTIVE:
            self._state = PlanState.PENDING
            logger.info("Plan Mode: INACTIVE → PENDING")

    def deactivate(self) -> None:
        """Request exit from Plan Mode.

        If a turn is in flight → EXIT_PENDING (waits for turn completion).
        Otherwise → INACTIVE directly.
        """
        if self._state == PlanState.PENDING:
            self._state = PlanState.INACTIVE
            logger.info("Plan Mode: PENDING → INACTIVE (cancelled before prompt)")
            return

        if self._state == PlanState.ACTIVE:
            if self._turn_in_flight:
                self._state = PlanState.EXIT_PENDING
                logger.info("Plan Mode: ACTIVE → EXIT_PENDING (turn in flight)")
            else:
                self._state = PlanState.INACTIVE
                logger.info("Plan Mode: ACTIVE → INACTIVE")

    def on_prompt_arrived(self) -> str:
        """Handle prompt arrival. **Idempotent** — only transitions from PENDING.

        When in PENDING state:
            - Transitions to ACTIVE
            - Returns plan mode instruction text for injection into conversation

        Returns:
            Plan mode instructions string, or empty string if not in PENDING.
        """
        if self._state == PlanState.PENDING:
            self._state = PlanState.ACTIVE
            self._turn_in_flight = True
            logger.info("Plan Mode: PENDING → ACTIVE")
            return _PLAN_MODE_INSTRUCTIONS.format(
                plan_file=str(self._plan_file_path),
            )

        if self._state == PlanState.ACTIVE:
            self._turn_in_flight = True

        return ""

    def on_turn_complete(self) -> str | None:
        """Handle turn completion.

        If EXIT_PENDING:
            - Transitions to INACTIVE
            - Returns exit reminder for injection

        Otherwise:
            - Clears turn_in_flight flag
            - Returns None

        Returns:
            Exit reminder string, or None.
        """
        self._turn_in_flight = False

        if self._state == PlanState.EXIT_PENDING:
            self._state = PlanState.INACTIVE
            logger.info("Plan Mode: EXIT_PENDING → INACTIVE (turn complete)")
            return _EXIT_REMINDER

        return None

    # ── Queries ─────────────────────────────────────────────────────────────

    def is_active(self) -> bool:
        """Check if Plan Mode is currently ACTIVE (write tools blocked).

        Returns:
            True if state is ACTIVE.
        """
        return self._state == PlanState.ACTIVE

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a tool call is permitted in the current state.

        While ACTIVE:
            - Read tools (file_read, file_search, web_fetch) → allowed
            - Write tools (file_edit, file_write, shell_exec) → blocked
            - Unknown tools → allowed (e.g., "skill" tool)

        While not ACTIVE:
            - All tools allowed.

        Args:
            tool_name: Name of the tool being called.

        Returns:
            True if the tool is allowed, False if blocked.
        """
        if not self.is_active():
            return True

        if tool_name in _WRITE_TOOLS:
            logger.debug("Plan Mode ACTIVE: blocked '%s'", tool_name)
            return False

        return True

    def get_plan_file_path(self) -> Path:
        """Return the path to the plan file for model reference.

        Returns:
            Path object for the plan file.
        """
        return self._plan_file_path

    @property
    def state(self) -> PlanState:
        """Current Plan Mode state (read-only)."""
        return self._state


# ── exit_plan_mode tool ──────────────────────────────────────────────────────
# [INT-06] 2026-08-09: Moved from cli/assembly.py. The exit_plan_mode tool
# and its ExitPlanModeInput model belong with the Plan Mode FSM, not in the
# CLI assembly root. assembly.py now calls make_exit_plan_mode_tool().


class ExitPlanModeInput(BaseModel):
    """Input for the exit_plan_mode tool."""

    plan: str = Field(
        description="The implementation plan summary to submit for approval",
    )


def make_exit_plan_mode_tool(plan_tracker: PlanModeTracker):
    """Build the exit_plan_mode tool handler bound to a plan tracker.

    Returns a tuple ``(ExitPlanModeInput, handler)`` for registration
    via ``registry.register("exit_plan_mode", input_model, ...)(handler)``.
    """
    async def exit_plan_mode(input: ExitPlanModeInput) -> str:
        plan_tracker.deactivate()
        return (
            "Plan Mode deactivated. You may now execute write operations "
            "and begin implementation."
        )

    return ExitPlanModeInput, exit_plan_mode
