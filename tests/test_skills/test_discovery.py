# [Phase 3] 2026-07-27: Tests for skills/discovery.py — SKILL.md file scanning
"""Tests for SKILL.md discovery/scanner."""

import tempfile
from pathlib import Path

import pytest

from code_agent.skills.discovery import discover_skills
from code_agent.skills.models import SkillInfo


def _create_skill_dir(base: Path, name: str, subdir: str = "") -> Path:
    """Create a SKILL.md in a temp directory structure.

    If subdir is empty, creates {base}/{name}/SKILL.md.
    Otherwise creates {base}/{subdir}/{name}/SKILL.md.
    """
    if subdir:
        skill_dir = base / subdir / name
    else:
        skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    md = skill_dir / "SKILL.md"
    md.write_text(
        f"---\n"
        f"name: {name}\n"
        f"description: A skill called {name}\n"
        f"---\n"
        f"# {name}\n"
        f"Do something useful.\n",
        encoding="utf-8",
    )
    return md


class TestDiscoverSkills:
    """Test SKILL.md file discovery."""

    @pytest.mark.asyncio
    async def test_discovers_skills_in_project_dir(self):
        """Skills in .code-agent/skills/ are discovered."""
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp) / "project"
            cwd.mkdir()

            skills_dir = cwd / ".code-agent" / "skills"
            _create_skill_dir(skills_dir, "commit")
            _create_skill_dir(skills_dir, "review")

            skills = await discover_skills(cwd=cwd)
            names = {s.name for s in skills}

            assert len(skills) == 2
            assert "commit" in names
            assert "review" in names

            for s in skills:
                assert isinstance(s, SkillInfo)
                assert s.scope == "project"
                assert s.body.strip()

    @pytest.mark.asyncio
    async def test_discovers_skills_in_nested_dirs(self):
        """Skills in nested directories (up to depth 5) are discovered."""
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp) / "project"
            cwd.mkdir()

            skills_dir = cwd / ".code-agent" / "skills"
            _create_skill_dir(skills_dir, "deep", subdir="a/b/c/d")

            skills = await discover_skills(cwd=cwd)
            names = {s.name for s in skills}

            assert len(skills) == 1
            assert "deep" in names

    @pytest.mark.asyncio
    async def test_deduplicates_by_name_project_wins(self):
        """Same-named skills: project overrides user (first found wins)."""
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp) / "project"
            cwd.mkdir()

            # Project skill
            proj_skills = cwd / ".code-agent" / "skills"
            _create_skill_dir(proj_skills, "test-skill")

            # User skill (same name)
            user_skills = Path(tmp) / "user-skills"
            _create_skill_dir(user_skills, "test-skill")

            skills = await discover_skills(
                cwd=cwd, user_skills_dir=user_skills,
            )
            assert len(skills) == 1
            # First found wins: project comes first in scan order
            assert skills[0].scope == "project"

    @pytest.mark.asyncio
    async def test_empty_dir_returns_empty(self):
        """Empty skills directory returns empty list."""
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp) / "project"
            cwd.mkdir()
            (cwd / ".code-agent" / "skills").mkdir(parents=True)

            skills = await discover_skills(cwd=cwd)
            assert skills == []

    @pytest.mark.asyncio
    async def test_no_skills_dir_returns_empty(self):
        """No .code-agent/skills/ directory returns empty list."""
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp) / "empty-project"
            cwd.mkdir()

            skills = await discover_skills(cwd=cwd)
            assert skills == []

    @pytest.mark.asyncio
    async def test_invalid_skill_md_is_skipped(self):
        """Invalid SKILL.md files are skipped with warning, not crashed."""
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp) / "project"
            cwd.mkdir()

            skills_dir = cwd / ".code-agent" / "skills"
            good_dir = skills_dir / "good"
            good_dir.mkdir(parents=True)
            (good_dir / "SKILL.md").write_text(
                "---\nname: good\ndescription: works\n---\nBody\n",
                encoding="utf-8",
            )

            bad_dir = skills_dir / "bad"
            bad_dir.mkdir(parents=True)
            (bad_dir / "SKILL.md").write_text(
                "No frontmatter at all, just text.\n", encoding="utf-8",
            )

            skills = await discover_skills(cwd=cwd)
            # Only the valid skill should be returned
            assert len(skills) == 1
            assert skills[0].name == "good"
