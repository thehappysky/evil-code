# [Phase 7] 2026-08-04: task tool — spawn a sub-agent
"""task tool — delegate a self-contained piece of work to a sub-agent.

References:
    - Rust TaskTool (xai-grok-tools/task)
    - design-docs/Python-Agent-补足方案-设计.md §Phase 7

The sub-agent runs in an isolated context (own system prompt + the task
prompt only) with a capability-filtered toolset. Its final assistant text is
returned to the parent as a tool result.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Literal

from pydantic import BaseModel, Field


class TaskInput(BaseModel):
    """Input for the task tool."""

    prompt: str = Field(
        description=(
            "The complete, self-contained task for the sub-agent. Include "
            "all necessary context (file paths, goals, constraints)."
        ),
    )
    subagent_type: str = Field(
        default="general-purpose",
        description=(
            "Role for the sub-agent: 'general-purpose', 'explore' "
            "(focused research), or 'plan'."
        ),
    )
    capability_mode: Literal["read-only", "read-write", "execute"] = Field(
        default="read-write",
        description=(
            "Toolset the sub-agent may use: 'read-only', 'read-write', "
            "or 'execute' (adds shell access)."
        ),
    )


class TaskTool:
    """Handle for the ``task`` tool, bound to a spawn callable."""

    def __init__(self, spawn: Callable[[str, str, str], Awaitable[str]]):
        self._spawn = spawn

    async def run(self, input: TaskInput) -> str:
        """Spawn the sub-agent and return its final text.

        Args:
            input: TaskInput.

        Returns:
            The sub-agent result, wrapped for the model.
        """
        result = await self._spawn(
            input.prompt, input.subagent_type, input.capability_mode,
        )
        return (
            f"<subagent_result type=\"{input.subagent_type}\">\n"
            f"{result}\n"
            f"</subagent_result>"
        )
