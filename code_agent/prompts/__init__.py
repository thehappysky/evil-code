# [PRM-01] 2026-08-09: prompts/ package — System Prompt / 指令层.
#
# Consolidates three previously scattered files into one package:
#   - prompts/main.py      (was code_agent/prompts.py)      — system prompt text
#   - prompts/rules.py     (was code_agent/rules.py)        — AGENTS.md/CLAUDE.md discovery
#   - prompts/reminders.py (was code_agent/core/reminders.py) — <system-reminder> generation
#
# ``__init__.py`` re-exports the public symbols of ``main`` so that existing
# ``from code_agent.prompts import X`` imports keep working unchanged.
# ``rules`` and ``reminders`` are accessed via their submodule paths:
#   ``from code_agent.prompts.rules import discover_project_rules``
#   ``from code_agent.prompts.reminders import ReminderInjector``
"""System Prompt / 指令层 package — prompts, rules, reminders."""

from code_agent.prompts.main import (
    COMPACT_SYSTEM_PROMPT,
    GENERAL_SUBAGENT_PROMPT,
    INTERJECTION_TEMPLATE,
    MAIN_SYSTEM_PROMPT,
    PLAN_MODE_EXIT_REMINDER,
    PLAN_MODE_INSTRUCTIONS,
    SUBAGENT_PROMPTS,
    SUMMARIZE_SYSTEM_PROMPT,
    TASK_COMPLETION_DISCIPLINE,
    TODO_GATE_REMINDER,
)

__all__ = [
    "COMPACT_SYSTEM_PROMPT",
    "GENERAL_SUBAGENT_PROMPT",
    "INTERJECTION_TEMPLATE",
    "MAIN_SYSTEM_PROMPT",
    "PLAN_MODE_EXIT_REMINDER",
    "PLAN_MODE_INSTRUCTIONS",
    "SUBAGENT_PROMPTS",
    "SUMMARIZE_SYSTEM_PROMPT",
    "TASK_COMPLETION_DISCIPLINE",
    "TODO_GATE_REMINDER",
]
