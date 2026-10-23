# [Phase 5 / Phase 6] 2026-08-04: DoomLoopHook — repeated tool-call detection
"""Doom loop detection — breaks the "call the same tool forever" failure mode.

References:
    - design-docs/Grok-Build-LLM缺陷防御体系深度分析.md 缺陷 5（死循环）
    - Rust Doom Loop detection + resample (xai-grok-shell)

If the model emits the *same* tool call (same name + canonical arguments) N
times in a row, the hook DENYs further repeats. The model receives a
"permission denied" error result and is forced to change its approach —
cheaper and safer than letting it burn tokens retrying a stuck action.

Semantics: the detector is sticky-but-recoverable. After a DENY, the next
*different* call slides the window, so one genuine attempt at a new approach
earns the model another shot at the repeated tool.
"""

from __future__ import annotations

import json
import logging
from collections import deque

from code_agent.core.hooks import HookDecision

logger = logging.getLogger(__name__)


class DoomLoopHook:
    """DENY a tool call repeated consecutively past ``repeat_limit`` times."""

    def __init__(self, repeat_limit: int = 3):
        self._repeat_limit = repeat_limit
        self._recent: deque[str] = deque(maxlen=repeat_limit)

    def _signature(self, tool_name: str, arguments: dict) -> str:
        """Canonical signature: tool name + sorted/stable JSON of arguments."""
        try:
            args_str = json.dumps(arguments or {}, sort_keys=True, default=str)
        except Exception:
            args_str = repr(arguments)
        return f"{tool_name}|{args_str}"

    async def on_tool_call(
        self, tool_name: str, arguments: dict,
    ) -> HookDecision:
        """Return DENY when the exact same call repeats past the limit."""
        sig = self._signature(tool_name, arguments)
        repeated = (
            len(self._recent) == self._repeat_limit
            and all(s == sig for s in self._recent)
        )
        # Always slide the window (even on DENY) so the guard is recoverable.
        self._recent.append(sig)
        if repeated:
            logger.warning(
                "Doom loop detected: '%s' repeated %d times; denying",
                tool_name, self._repeat_limit,
            )
            return HookDecision.DENY
        return HookDecision.ALLOW
