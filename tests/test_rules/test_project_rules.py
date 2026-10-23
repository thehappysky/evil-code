# [Phase 5] 2026-08-05: Tests for project rules discovery + hook
"""Tests for code_agent/rules.py and hooks/project_rules_hook.py."""

from __future__ import annotations

from code_agent.core.session import Session
from code_agent.hooks.project_rules_hook import ProjectRulesHook
from code_agent.prompts.rules import discover_project_rules, format_project_context


class TestDiscoverProjectRules:
    def test_finds_agents_md(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Always use type hints.", encoding="utf-8")
        assert discover_project_rules(tmp_path) == "Always use type hints."

    def test_finds_claude_md(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("Prefer composition.", encoding="utf-8")
        assert discover_project_rules(tmp_path) == "Prefer composition."

    def test_agents_md_preferred_over_claude_md(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("agents rules", encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text("claude rules", encoding="utf-8")
        assert discover_project_rules(tmp_path) == "agents rules"

    def test_missing_returns_none(self, tmp_path):
        assert discover_project_rules(tmp_path) is None

    def test_empty_file_returns_none(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("   \n", encoding="utf-8")
        assert discover_project_rules(tmp_path) is None

    def test_format_project_context(self):
        out = format_project_context("rules")
        assert "<project-context>" in out
        assert "rules" in out
        assert "</project-context>" in out


class TestProjectRulesHook:
    async def test_injects_rules_on_session_start(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Use my conventions.", encoding="utf-8")
        hook = ProjectRulesHook(str(tmp_path))
        session = Session(system_prompt="T")
        await hook.on_session_start(session)

        texts = [m.content for m in session.messages if m.role == "user"]
        assert any("<project-context>" in t and "Use my conventions." in t for t in texts)

    async def test_injects_once(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("x", encoding="utf-8")
        hook = ProjectRulesHook(str(tmp_path))
        session = Session(system_prompt="T")
        await hook.on_session_start(session)
        await hook.on_session_start(session)  # second call is a no-op
        texts = [m.content for m in session.messages if m.role == "user"]
        assert sum("x" in t for t in texts) == 1

    async def test_no_rules_no_injection(self, tmp_path):
        hook = ProjectRulesHook(str(tmp_path))
        session = Session(system_prompt="T")
        await hook.on_session_start(session)
        assert len(session.messages) == 1  # only system

    async def test_injects_in_agent_loop(self, tmp_path):
        """Rules appear in the session when a real Agent runs."""
        from code_agent.core.agent import Agent
        from code_agent.tools.registry import ToolRegistry

        from conftest import MockChatClient, make_text_response

        (tmp_path / "AGENTS.md").write_text("Project golden rule.", encoding="utf-8")
        client = MockChatClient()
        client.set_responses([make_text_response("done")])
        registry = ToolRegistry()
        registry.finalize()
        agent = Agent(
            Session(system_prompt="T"), client, registry,
            max_turns=3, hooks=[ProjectRulesHook(str(tmp_path))],
        )
        async for _ in agent.run("hi"):
            pass

        texts = [m.content for m in agent._session.messages if m.role == "user"]
        assert any("Project golden rule." in t for t in texts)
