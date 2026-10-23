# [Phase 3] 2026-07-27: Skills system package
# Public API: SkillManager, discover_skills, parse_skill_md, SkillInfo, SkillTool

from code_agent.skills.models import SkillInfo
from code_agent.skills.parser import parse_skill_md
from code_agent.skills.discovery import discover_skills
from code_agent.skills.manager import SkillManager, SkillTool

__all__ = [
    "SkillInfo",
    "parse_skill_md",
    "discover_skills",
    "SkillManager",
    "SkillTool",
]
