# [Phase 3] 2026-07-27: SkillInfo — Pydantic data model for parsed SKILL.md files
"""SkillInfo Pydantic data model.

References:
    - design-docs/Python-Agent-MVP-Phase3-详细设计.md §7 skills/models.py
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SkillInfo(BaseModel):
    """Parsed SKILL.md file information.

    Each SKILL.md has a YAML frontmatter block followed by Markdown body.
    This model holds the parsed result.

    Attributes:
        name: Skill name, e.g. "commit", "review-pr". Used as the unique key.
        description: Short description shown in <available_skills> listing.
        body: Markdown body — the instructions the model follows when the skill is invoked.
        path: Absolute path to the SKILL.md file on disk.
        scope: Discovery scope — "project", "user", or "global".
        when_to_use: Optional trigger hint for model decision-making.
        allowed_tools: Optional tool whitelist for this skill.
    """

    name: str = Field(description="Skill name (e.g., 'commit', 'review-pr')")
    description: str = Field(description="Short description for available_skills listing")
    body: str = Field(description="Markdown body — instructions for the model")
    path: str = Field(description="Absolute file path of the SKILL.md")
    scope: str = Field(default="project", description="Discovery scope: project | user | global")
    when_to_use: str = Field(default="", description="Trigger hint for model decision-making")
    allowed_tools: list[str] = Field(
        default_factory=list,
        description="Tool whitelist for skill execution",
    )
