# [Phase 3] 2026-07-27: Tests for skills/models.py — SkillInfo Pydantic model
"""Tests for SkillInfo Pydantic model."""

import pytest
from pydantic import ValidationError

from code_agent.skills.models import SkillInfo


class TestSkillInfo:
    """Test SkillInfo model creation and validation."""

    def test_minimal_creation(self):
        """SkillInfo can be created with minimal fields."""
        skill = SkillInfo(
            name="test-skill",
            description="A test skill",
            body="# Instructions\nDo something",
            path="/path/to/SKILL.md",
        )
        assert skill.name == "test-skill"
        assert skill.description == "A test skill"
        assert skill.body == "# Instructions\nDo something"
        assert skill.path == "/path/to/SKILL.md"
        assert skill.scope == "project"  # default
        assert skill.when_to_use == ""  # default
        assert skill.allowed_tools == []  # default

    def test_full_creation(self):
        """SkillInfo with all fields."""
        skill = SkillInfo(
            name="commit",
            description="Create git commits",
            body="# Instructions\n1. git diff",
            path="/home/user/.code-agent/skills/commit/SKILL.md",
            scope="user",
            when_to_use="user asks to commit",
            allowed_tools=["bash", "file_read"],
        )
        assert skill.scope == "user"
        assert skill.when_to_use == "user asks to commit"
        assert skill.allowed_tools == ["bash", "file_read"]

    def test_missing_name_raises_error(self):
        """Missing name raises pydantic validation error."""
        with pytest.raises(ValidationError):
            SkillInfo(
                description="test",
                body="body",
                path="/p",
            )

    def test_missing_description_raises_error(self):
        """Missing description raises pydantic validation error."""
        with pytest.raises(ValidationError):
            SkillInfo(
                name="test",
                body="body",
                path="/p",
            )
