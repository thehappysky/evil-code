# [Phase 3] 2026-07-27: PlanModeHook — Plan Mode integration via AgentHook interface
"""Phase 3 AgentHook implementation: PlanModeHook.

References:
    - design-docs/Python-Agent-MVP-Phase3-详细设计.md §12 PlanModeHook

Wraps PlanModeTracker as an AgentHook so Plan Mode integrates into
the Agent lifecycle without modifying core/agent.py.

Tool interception logic:
    - ACTIVE state + write tool (file_edit, file_write, shell_exec) → DENY
    - ACTIVE state + read tool (file_read, file_search, web_fetch) → ALLOW
    - Non-ACTIVE state → ALLOW (no interception)

Exception handling: follows the two-layer defense contract:
    - on_turn_start / on_turn_end: catch + log.warning + return None
    - on_tool_call: catch + log.error + return DENY (fail-safe)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from code_agent.core.hooks import AgentHook, HookDecision

if TYPE_CHECKING:
    from code_agent.core.plan_mode import PlanModeTracker
    from code_agent.core.session import Session

logger = logging.getLogger(__name__)


def _same_file(target: str, plan_file: Path) -> bool:
    """Whether a file path argument resolves to the plan file.

    Used to let the model write its plan while write tools are otherwise
    blocked in Plan Mode.
    """
    try:
        return (
            Path(target).expanduser().resolve()
            == Path(plan_file).expanduser().resolve()
        )
    except Exception:
        return False


class PlanModeHook:
    """Adapts PlanModeTracker to the AgentHook interface.

    Plan Mode's tool interception logic:
    - ACTIVE + write tool → DENY
    - ACTIVE + read tool → ALLOW
    - Non-ACTIVE → ALLOW

    Instruction injection: uses session.add_user_message() (same channel as
    MemoryHook's synthetic system-reminder). This keeps the system prompt
    clean and treats plan instructions as context rather than command.
    """

    def __init__(self, tracker: "PlanModeTracker"):
        self._tracker = tracker

    # ── AgentHook methods ──────────────────────────────────────────────────

    async def on_turn_start(
        self, session: "Session", turn: int, tools: list[dict]
    ) -> None:
        """If Plan Mode just became active, inject plan instructions.

        Uses session.add_user_message() as the injection channel —
        the same channel as MemoryHook's synthetic system-reminder.

        on_prompt_arrived() is idempotent: when NOT in PENDING state,
        it returns "" and no state transition occurs.
        """
        try:
            instructions = self._tracker.on_prompt_arrived()
            if instructions:
                session.add_user_message(instructions)
                logger.info(
                    "Plan Mode instructions injected (turn %d)", turn,
                )
        except Exception:
            logger.warning(
                "PlanModeHook.on_turn_start failed; Plan Mode state may be stale",
                exc_info=True,
            )

    async def on_tool_call(
        self, tool_name: str, arguments: dict,
    ) -> HookDecision:
        """Block write tools while Plan Mode is ACTIVE.

        Deny rules:
            - file_edit → DENY
            - file_write → DENY
            - shell_exec → DENY

        Exception (2026-08-04): writing the plan file itself is allowed so
        the model can fulfill the "Write your plan to {plan_file}"
        instruction instead of hitting a deadlock.
        """
        try:
            if self._tracker.is_active():
                # Allow writing the plan file itself.
                if tool_name in ("file_edit", "file_write"):
                    target = arguments.get("file_path")
                    if target and _same_file(
                        str(target), self._tracker.get_plan_file_path(),
                    ):
                        return HookDecision.ALLOW

                if not self._tracker.is_tool_allowed(tool_name):
                    logger.info(
                        "Plan Mode ACTIVE: denied '%s'", tool_name,
                    )
                    return HookDecision.DENY
        except Exception:
            logger.error(
                "PlanModeHook.on_tool_call exception — treating as DENY",
                exc_info=True,
            )
            return HookDecision.DENY

        return HookDecision.ALLOW

    async def on_turn_end(
        self, session: "Session", turn: int,
    ) -> None:
        """Handle Plan Mode exit: EXIT_PENDING → INACTIVE + inject reminder.

        The exit reminder is injected as a synthetic user message so
        the model sees it on the next turn.
        """
        try:
            reminder = self._tracker.on_turn_complete()
            if reminder:
                session.add_user_message(reminder)
                logger.info(
                    "Plan Mode exit reminder injected (turn %d)", turn,
                )
        except Exception:
            logger.warning(
                "PlanModeHook.on_turn_end failed; Plan Mode may not exit cleanly",
                exc_info=True,
            )
