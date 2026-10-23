# [Phase 3] 2026-07-27: SKILL.md file scanner — discover skills from project + user directories
"""SKILL.md file scanner.

References:
    - design-docs/Python-Agent-MVP-Phase3-详细设计.md §7 skills/discovery.py

Scans filesystem for SKILL.md files in:
    1. {cwd}/.code-agent/skills/** /SKILL.md  (project scope)
    2. ~/.code-agent/skills/** /SKILL.md       (user scope)

Max recursion depth: 5 directory levels.
Deduplicates by skill name (first found wins, sorted by path depth).
"""

from __future__ import annotations

import logging
from pathlib import Path

from code_agent.skills.models import SkillInfo
from code_agent.skills.parser import parse_skill_md

logger = logging.getLogger(__name__)

# Maximum directory depth for SKILL.md discovery
_MAX_DEPTH = 5

# Filename to look for
_SKILL_FILENAME = "SKILL.md"


async def discover_skills(
    cwd: Path,
    user_skills_dir: Path | None = None,
) -> list[SkillInfo]:
    """Scan filesystem for SKILL.md files.

    Strategy:
    1. Walk project directory: {cwd}/.code-agent/skills/
    2. Walk user directory: ~/.code-agent/skills/
    3. Each directory: find SKILL.md files, parse frontmatter + body
    4. Deduplicate by name (first found wins, sorted by path depth)

    Args:
        cwd: Current working directory (project root).
        user_skills_dir: Optional override for user skills directory.
            Defaults to ~/.code-agent/skills/.

    Returns:
        List of SkillInfo, deduplicated by name. Project skills override
        user skills for same-name conflicts.
    """
    import os

    if user_skills_dir is None:
        user_skills_dir = Path.home() / ".code-agent" / "skills"

    project_skills_dir = cwd / ".code-agent" / "skills"

    # Collect all found skills: project first, then user
    all_skills: list[SkillInfo] = []

    # Step 1: Project directory
    if project_skills_dir.exists():
        project_skills = _scan_directory(project_skills_dir, scope="project")
        all_skills.extend(project_skills)
        logger.debug("Found %d skill(s) in project directory", len(project_skills))

    # Step 2: User directory
    if user_skills_dir.exists():
        user_skills = _scan_directory(user_skills_dir, scope="user")
        all_skills.extend(user_skills)
        logger.debug("Found %d skill(s) in user directory", len(user_skills))

    # Step 3: Deduplicate by name (first found wins — project skills come first)
    deduped = _deduplicate_by_name(all_skills)

    logger.info(
        "Discovered %d skills (%d project + %d user → %d unique)",
        len(all_skills),
        sum(1 for s in all_skills if s.scope == "project"),
        sum(1 for s in all_skills if s.scope == "user"),
        len(deduped),
    )

    return deduped


# ── Internal helpers ───────────────────────────────────────────────────────────


def _scan_directory(
    root: Path,
    scope: str = "project",
    depth: int = 0,
) -> list[SkillInfo]:
    """Recursively scan a directory for SKILL.md files.

    Args:
        root: Directory to scan.
        scope: Skill scope string ("project" or "user").
        depth: Current recursion depth (starts at 0).

    Returns:
        List of successfully parsed SkillInfo objects.
        Files that fail to parse are skipped with a warning.
    """
    if depth > _MAX_DEPTH:
        return []

    skills: list[SkillInfo] = []

    try:
        entries = list(root.iterdir())
    except (OSError, PermissionError) as exc:
        logger.warning("Cannot read skills directory %s: %s", root, exc)
        return []

    for entry in entries:
        if entry.name.startswith("."):
            continue

        if entry.is_file() and entry.name == _SKILL_FILENAME:
            try:
                skill = parse_skill_md(entry)
                skill.scope = scope  # Override inferred scope with discovery scope
                skills.append(skill)
                logger.debug("Parsed skill '%s' from %s", skill.name, entry)
            except (ValueError, OSError) as exc:
                logger.warning("Skipping invalid SKILL.md at %s: %s", entry, exc)

        elif entry.is_dir() and depth < _MAX_DEPTH:
            sub_skills = _scan_directory(entry, scope=scope, depth=depth + 1)
            skills.extend(sub_skills)

    return skills


def _deduplicate_by_name(skills: list[SkillInfo]) -> list[SkillInfo]:
    """Deduplicate skills by name, keeping the first occurrence.

    First found wins — project skills come first in the input list
    (added before user skills), so project always overrides user
    for same-name conflicts.

    Args:
        skills: List of skills, possibly with duplicate names.

    Returns:
        Deduplicated list preserving original order.
    """
    seen: set[str] = set()
    result: list[SkillInfo] = []

    for skill in skills:
        if skill.name not in seen:
            seen.add(skill.name)
            result.append(skill)
        else:
            logger.debug(
                "Skill '%s' from %s shadowed by earlier discovery (same name)",
                skill.name, skill.path,
            )

    return result
