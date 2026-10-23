# [Phase 5] 2026-08-05: Centralized prompts wiring tests
"""Verify the system prompts are centralized, wired, and carry the Evil identity.

Prompt *effectiveness* is semantic and cannot be asserted; what CAN be proven:
- the prompts exist and are non-empty,
- the identity is "Evil" throughout,
- each subsystem references the centralized prompt (not a stale copy),
- the key instruction blocks survived the port.
"""

from __future__ import annotations

import code_agent.cli.main
import code_agent.compaction.summarizer
import code_agent.core.plan_mode
from code_agent import prompts


class TestMainSystemPrompt:
    def test_identity_is_evil(self):
        assert "Evil" in prompts.MAIN_SYSTEM_PROMPT
        assert "neuro" not in prompts.MAIN_SYSTEM_PROMPT.lower()

    def test_key_blocks_present(self):
        for block in ("<action_safety>", "<tool_calling>",
                      "<output_efficiency>", "<formatting>",
                      "<task_completion_discipline>"):
            assert block in prompts.MAIN_SYSTEM_PROMPT

    def test_compaction_prompt(self):
        assert "Evil" in prompts.COMPACT_SYSTEM_PROMPT
        assert prompts.COMPACT_SYSTEM_PROMPT.strip()


class TestSubagentPrompts:
    def test_roles_present_and_nonempty(self):
        assert set(prompts.SUBAGENT_PROMPTS) == {"general-purpose", "explore", "plan"}
        for role, text in prompts.SUBAGENT_PROMPTS.items():
            assert text.strip(), role

    def test_read_only_roles(self):
        assert "READ-ONLY MODE" in prompts.SUBAGENT_PROMPTS["explore"]
        assert "READ-ONLY MODE" in prompts.SUBAGENT_PROMPTS["plan"]
        # explore has no editing tools.
        assert "NO file editing tools" in prompts.SUBAGENT_PROMPTS["explore"]


class TestOtherPrompts:
    def test_summarizer_prompt(self):
        assert "Evil" in prompts.SUMMARIZE_SYSTEM_PROMPT
        assert "Return ONLY the summary text" in prompts.SUMMARIZE_SYSTEM_PROMPT

    def test_plan_mode_prompt(self):
        assert "exit_plan_mode" in prompts.PLAN_MODE_INSTRUCTIONS
        assert "{plan_file}" in prompts.PLAN_MODE_INSTRUCTIONS  # formatted later


class TestWiring:
    def test_cli_uses_central_main_prompt(self):
        assert code_agent.cli.main._MAIN_SYSTEM_PROMPT is prompts.MAIN_SYSTEM_PROMPT

    def test_subagent_factory_uses_central_prompts(self):
        assert code_agent.cli.main.SUBAGENT_PROMPTS is prompts.SUBAGENT_PROMPTS

    def test_plan_mode_uses_central_prompts(self):
        assert code_agent.core.plan_mode._PLAN_MODE_INSTRUCTIONS is prompts.PLAN_MODE_INSTRUCTIONS
        assert code_agent.core.plan_mode._EXIT_REMINDER is prompts.PLAN_MODE_EXIT_REMINDER

    def test_summarizer_uses_central_prompt(self):
        assert code_agent.compaction.summarizer._SUMMARIZE_SYSTEM_PROMPT is prompts.SUMMARIZE_SYSTEM_PROMPT
