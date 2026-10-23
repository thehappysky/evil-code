# [Phase 3] 2026-07-27: Tests for skills/manager.py — SkillManager + SkillTool
"""Tests for SkillManager and SkillTool."""

import tempfile
import time
from pathlib import Path

import pytest

from code_agent.skills.manager import SkillManager, SkillTool, SkillToolInput
from code_agent.skills.models import SkillInfo


def _make_skill(
    name: str,
    desc: str,
    body: str,
    path: str = "/test/SKILL.md",
    when_to_use: str = "",
) -> SkillInfo:
    """Helper to create a SkillInfo for testing."""
    return SkillInfo(
        name=name, description=desc, body=body, path=path, scope="project",
        when_to_use=when_to_use,
    )


class TestSkillTool:
    """Test SkillTool — the bridge between model and SkillManager."""

    @pytest.mark.asyncio
    async def test_run_returns_formatted_skill(self):
        """SkillTool.run returns XML-wrapped skill body."""
        mgr = SkillManager(cwd=Path("/fake"))
        skill = _make_skill("commit", "Create commits", "# Instructions\n1. git diff")
        mgr._skills["commit"] = skill

        tool = SkillTool(manager=mgr)
        result = await tool.run(SkillToolInput(skill="commit"))

        assert "<skill name=" in result
        assert 'description="Create commits"' in result
        assert "# Instructions" in result
        assert "git diff" in result
        assert "</skill>" in result

    @pytest.mark.asyncio
    async def test_run_not_found_returns_available_list(self):
        """SkillTool.run returns helpful message when skill not found."""
        mgr = SkillManager(cwd=Path("/fake"))
        mgr._skills["commit"] = _make_skill("commit", "Create commits", "body")
        mgr._skills["review"] = _make_skill("review", "Review PRs", "body")

        tool = SkillTool(manager=mgr)
        result = await tool.run(SkillToolInput(skill="nonexistent"))

        assert "not found" in result.lower()
        assert "commit" in result
        assert "review" in result


class TestSkillManager:
    """Test SkillManager lifecycle and formatting."""

    @pytest.mark.asyncio
    async def test_load_all_from_directory(self):
        """SkillManager.load_all discovers skills from project directory."""
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp) / "project"
            cwd.mkdir()

            skills_dir = cwd / ".code-agent" / "skills" / "hello"
            skills_dir.mkdir(parents=True)
            (skills_dir / "SKILL.md").write_text(
                "---\nname: hello\ndescription: Say hello\n---\nEcho hello world.\n",
                encoding="utf-8",
            )

            mgr = SkillManager(cwd=cwd)
            skills = await mgr.load_all()

            assert len(skills) == 1
            assert skills[0].name == "hello"
            assert mgr.get("hello") is not None
            assert mgr.count == 1

    def test_get_returns_none_for_unknown_skill(self):
        """SkillManager.get returns None for unknown skill."""
        mgr = SkillManager(cwd=Path("/fake"))
        assert mgr.get("nonexistent") is None

    def test_format_available_skills_empty(self):
        """format_available_skills returns empty string when no skills."""
        mgr = SkillManager(cwd=Path("/fake"))
        result = mgr.format_available_skills()
        assert result == ""

    def test_format_available_skills_with_skills(self):
        """format_available_skills formats all skills."""
        mgr = SkillManager(cwd=Path("/fake"))
        mgr._skills["commit"] = _make_skill(
            "commit", "Create commits", "body",
        )
        mgr._skills["test"] = _make_skill(
            "test", "Run tests", "body",
            when_to_use="user asks to test",
        )

        result = mgr.format_available_skills()
        assert "<available_skills>" in result
        assert "</available_skills>" in result
        assert "commit" in result
        assert "Create commits" in result
        assert "test" in result
        assert "Run tests" in result
        assert "user asks to test" in result

    def test_format_skill_invocation(self):
        """format_skill_invocation wraps skill in XML."""
        mgr = SkillManager(cwd=Path("/fake"))
        skill = _make_skill("commit", "Create commits", "# Steps\n1. diff")

        result = mgr.format_skill_invocation(skill)
        assert '<skill name="commit"' in result
        assert 'description="Create commits"' in result
        assert "# Steps" in result
        assert "1. diff" in result
        assert "</skill>" in result

    def test_names_property(self):
        """names property returns set of skill names."""
        mgr = SkillManager(cwd=Path("/fake"))
        assert mgr.names == set()

        mgr._skills["a"] = _make_skill("a", "A", "body")
        mgr._skills["b"] = _make_skill("b", "B", "body")
        assert mgr.names == {"a", "b"}

    def test_count_property(self):
        """count property returns number of skills."""
        mgr = SkillManager(cwd=Path("/fake"))
        assert mgr.count == 0

        mgr._skills["a"] = _make_skill("a", "A", "body")
        assert mgr.count == 1

    @pytest.mark.asyncio
    async def test_reload_detects_changes(self):
        """reload_if_changed detects modified SKILL.md files."""
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp) / "project"
            cwd.mkdir()

            skills_dir = cwd / ".code-agent" / "skills" / "test"
            skills_dir.mkdir(parents=True)
            (skills_dir / "SKILL.md").write_text(
                "---\nname: test\ndescription: v1\n---\nBody\n",
                encoding="utf-8",
            )

            mgr = SkillManager(cwd=cwd)
            await mgr.load_all()

            # Modify the SKILL.md (ensure mtime changes on all filesystems)
            time.sleep(0.1)
            (skills_dir / "SKILL.md").write_text(
                "---\nname: test\ndescription: v2 updated\n---\nNew body\n",
                encoding="utf-8",
            )

            changed = await mgr.reload_if_changed()
            assert len(changed) == 1
            assert mgr.get("test").description == "v2 updated"
