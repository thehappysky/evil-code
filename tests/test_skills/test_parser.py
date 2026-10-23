# [Phase 3] 2026-07-27: Tests for skills/parser.py — SKILL.md YAML frontmatter parsing
"""Tests for SKILL.md parser."""

import tempfile
from pathlib import Path

import pytest

from code_agent.skills.models import SkillInfo
from code_agent.skills.parser import parse_skill_md


class TestParseSkillMd:
    """Test SKILL.md parsing."""

    def test_parse_valid_skill_md(self):
        """Parse a well-formed SKILL.md file."""
        content = """---
name: commit
description: Create well-formatted git commits
when_to_use: user asks to commit
allowed_tools: [bash, file_read]
---
# Instructions
1. Run 'git diff --staged' to see changes
2. Write a concise commit message
3. Run 'git commit -m "message"'
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8",
        ) as f:
            f.write(content)
            path = Path(f.name)

        try:
            skill = parse_skill_md(path)
            assert isinstance(skill, SkillInfo)
            assert skill.name == "commit"
            assert skill.description == "Create well-formatted git commits"
            assert skill.when_to_use == "user asks to commit"
            assert skill.allowed_tools == ["bash", "file_read"]
            assert "git diff" in skill.body
            assert "commit -m" in skill.body
        finally:
            path.unlink(missing_ok=True)

    def test_parse_minimal_skill_md(self):
        """Parse a SKILL.md with only required fields."""
        content = """---
name: hello
description: Say hello
---
Hello world!
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8",
        ) as f:
            f.write(content)
            path = Path(f.name)

        try:
            skill = parse_skill_md(path)
            assert skill.name == "hello"
            assert skill.description == "Say hello"
            assert skill.body == "Hello world!"
            assert skill.when_to_use == ""
            assert skill.allowed_tools == []
        finally:
            path.unlink(missing_ok=True)

    def test_missing_name_raises_error(self):
        """Missing name field raises ValueError."""
        content = """---
description: No name here
---
Body
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8",
        ) as f:
            f.write(content)
            path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="name"):
                parse_skill_md(path)
        finally:
            path.unlink(missing_ok=True)

    def test_missing_description_raises_error(self):
        """Missing description field raises ValueError."""
        content = """---
name: test
---
Body
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8",
        ) as f:
            f.write(content)
            path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="description"):
                parse_skill_md(path)
        finally:
            path.unlink(missing_ok=True)

    def test_no_frontmatter_treats_entire_as_body(self):
        """File with no frontmatter: body is everything."""
        content = "# No frontmatter here\nJust plain markdown."
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8",
        ) as f:
            f.write(content)
            path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="name"):
                parse_skill_md(path)
        finally:
            path.unlink(missing_ok=True)

    def test_line_based_recovery_for_malformed_yaml(self):
        """Malformed YAML falls back to line-based recovery."""
        content = """---
name: test-skill
description: A description with: colons: everywhere
---
Body text here.
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8",
        ) as f:
            f.write(content)
            path = Path(f.name)

        try:
            skill = parse_skill_md(path)
            assert skill.name == "test-skill"
            assert "colons: everywhere" in skill.description
            assert skill.body.strip() == "Body text here."
        finally:
            path.unlink(missing_ok=True)

    def test_scope_is_inferred_from_path(self):
        """Scope is inferred from file path."""
        content = """---
name: test
description: test
---
Body
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8",
        ) as f:
            f.write(content)
            path = Path(f.name)

        try:
            skill = parse_skill_md(path)
            # Temp file → not under .code-agent → "global" scope
            assert skill.scope in ("project", "user", "global")
        finally:
            path.unlink(missing_ok=True)

    def test_allowed_tools_comma_separated(self):
        """Comma-separated allowed_tools is normalized to list."""
        content = """---
name: test
description: test
allowed_tools: bash, file_read, file_write
---
Body
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8",
        ) as f:
            f.write(content)
            path = Path(f.name)

        try:
            skill = parse_skill_md(path)
            assert skill.allowed_tools == ["bash", "file_read", "file_write"]
        finally:
            path.unlink(missing_ok=True)
