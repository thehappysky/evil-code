# [Phase 7] 2026-08-04: SubagentCoordinator — spawn and track sub-agents
"""Sub-agent coordinator.

References:
    - Rust SubagentCoordinator (xai-grok-shell/src/agent/subagent/)
    - design-docs/Python-Agent-补足方案-设计.md §Phase 7

The coordinator holds the depth counter and delegates actual sub-agent
construction + execution to a caller-provided ``spawn_fn`` (built in
cli/main.py, which owns the client/tools/session wiring).

Context isolation (Phase 7, first version):
    - The sub-agent gets ZERO parent context by default — a fresh Session
      with its own system prompt plus the task prompt as its only user
      message (matches Rust ``ContextSource::New``).
    - Recursion is prevented structurally: sub-agent tool registries are
      built WITHOUT the ``task`` tool, so a sub-agent cannot spawn another.
      The depth counter is a belt-and-suspenders second guard.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# Async (prompt, subagent_type, capability_mode) -> final text.
SpawnFn = Callable[[str, str, str], Awaitable[str]]


class SubagentCoordinator:
    """Depth-guarded sub-agent spawner.

    Args:
        spawn_fn: Async callable that builds and runs a sub-agent, returning
            its final text. Owns the client/tools/session lifecycle.
        max_depth: Maximum nesting depth (1 = sub-agents cannot spawn
            sub-sub-agents).
    """

    def __init__(self, spawn_fn: SpawnFn, *, max_depth: int = 1):
        self._spawn_fn = spawn_fn
        self._max_depth = max_depth
        self._depth = 0

    async def spawn(
        self,
        prompt: str,
        subagent_type: str = "general-purpose",
        capability_mode: str = "read-write",
    ) -> str:
        """Spawn a sub-agent and return its final text.

        Returns an error string (not an exception) when the depth limit is
        reached, so the parent model can react gracefully.

        Args:
            prompt: The complete task for the sub-agent.
            subagent_type: "general-purpose" | "explore" | "plan".
            capability_mode: "read-only" | "read-write" | "execute".

        Returns:
            The sub-agent's final assistant text, or a failure message.
        """
        if self._depth >= self._max_depth:
            logger.warning(
                "Subagent depth limit exceeded (depth=%d, max=%d)",
                self._depth, self._max_depth,
            )
            return (
                "Subagent depth limit exceeded: a sub-agent cannot spawn "
                "another sub-agent."
            )

        self._depth += 1
        try:
            return await self._spawn_fn(prompt, subagent_type, capability_mode)
        except Exception as exc:
            logger.warning("Subagent execution failed: %s", exc)
            return f"Subagent execution failed: {exc}"
        finally:
            self._depth -= 1

    @property
    def depth(self) -> int:
        return self._depth
