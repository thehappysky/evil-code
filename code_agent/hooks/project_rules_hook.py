# [Phase 5] 2026-08-05: ProjectRulesHook — inject AGENTS.md / CLAUDE.md at session start
"""Project rules hook.

Injects the workspace rules file (AGENTS.md / CLAUDE.md) into the session as
a ``<project-context>`` synthetic user message on session start — the same
channel MemoryHook uses — so the model sees the rules before it starts work.

Exception handling: rules discovery failure degrades gracefully (log + skip);
the agent continues without project rules.
"""

from __future__ import annotations

import logging

from code_agent.prompts.rules import discover_project_rules, format_project_context

logger = logging.getLogger(__name__)


class ProjectRulesHook:
    """AgentHook that injects project rules once per session."""

    def __init__(self, cwd: str):
        self._cwd = cwd
        self._injected: bool = False

    async def on_session_start(self, session) -> None:
        if self._injected:
            return
        try:
            rules = discover_project_rules(self._cwd)
            if rules:
                session.add_user_message(format_project_context(rules))
                self._injected = True
        except Exception:
            logger.warning("Project rules injection failed", exc_info=True)
