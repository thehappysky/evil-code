# [Phase 3] 2026-07-27: SkillManager + SkillTool — skill lifecycle management and model invocation
"""Skill lifecycle management.

References:
    - design-docs/Python-Agent-MVP-Phase3-详细设计.md §7 skills/manager.py
    - Rust SkillManager (skill_discovery_tracker/mod.rs)

SkillManager handles:
    1. Discover all SKILL.md files at startup (project + user dirs)
    2. Watch filesystem for changes → automatic rediscovery
    3. Format <available_skills> block for system prompt injection
    4. Format individual <skill> block when model invokes a skill

SkillTool is the bridge to ToolRegistry — the model calls the "skill" tool
and receives the skill body as an XML-wrapped response.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from code_agent.skills.discovery import discover_skills
from code_agent.skills.models import SkillInfo

logger = logging.getLogger(__name__)

# ── SkillTool Input ───────────────────────────────────────────────────────────


class SkillToolInput(BaseModel):
    """Input model for the Skill tool.

    The model calls this to invoke a user-defined skill by name,
    optionally passing arguments.
    """

    skill: str = Field(description="Name of the skill to invoke (e.g., 'commit')")
    args: str | None = Field(
        default=None,
        description="Optional arguments to pass to the skill",
    )


# ── SkillTool ─────────────────────────────────────────────────────────────────


class SkillTool:
    """Skill tool — allows the model to invoke user-defined skills.

    Registration name: "skill"

    Behavior:
    1. Looks up the named skill from SkillManager
    2. If found → returns <skill name="..." description="...">body</skill>
    3. If not found → returns "Skill 'xxx' not found. Available: [...]"

    This is a class (not a function) because it holds a reference to
    SkillManager. The `run` method is used as the ToolRegistry handler.
    """

    def __init__(self, manager: "SkillManager"):
        self._manager = manager

    async def run(self, input: SkillToolInput) -> str:
        """Execute the skill tool.

        Args:
            input: SkillToolInput with skill name and optional args.

        Returns:
            Formatted skill body XML or "not found" message.
        """
        skill = self._manager.get(input.skill)
        if skill is None:
            available = ", ".join(sorted(self._manager.names))
            return (
                f"Skill '{input.skill}' not found. "
                f"Available: [{available}]"
            )

        return self._manager.format_skill_invocation(skill)


# ── SkillManager ──────────────────────────────────────────────────────────────


class SkillManager:
    """Skill lifecycle management.

    Responsibilities:
    1. Discover all SKILL.md files at startup (project + user dirs)
    2. Store loaded skills and provide lookup
    3. Format <available_skills> block for system prompt injection
    4. Format individual <skill> block when model invokes a skill
    5. Support hot-reload via mtime checking

    Usage::

        mgr = SkillManager(cwd=Path.cwd())
        await mgr.load_all()
        system_prompt += mgr.format_available_skills()

        # In _build_tools():
        skill_tool = SkillTool(manager=mgr)
        registry.register("skill", SkillToolInput, ...)(skill_tool.run)
    """

    def __init__(self, cwd: Path):
        self._cwd = cwd
        self._skills: dict[str, SkillInfo] = {}
        self._mtime_cache: dict[str, float] = {}  # path → last seen mtime

    # ── Discovery ──────────────────────────────────────────────────────────

    async def load_all(self) -> list[SkillInfo]:
        """Scan and load all SKILL.md files.

        Deduplicates by skill name (project overrides user).

        Returns:
            List of all discovered SkillInfo objects.
        """
        skills = await discover_skills(self._cwd)
        self._skills = {s.name: s for s in skills}

        # Update mtime cache
        for s in skills:
            try:
                self._mtime_cache[s.path] = Path(s.path).stat().st_mtime
            except OSError:
                self._mtime_cache[s.path] = 0.0

        logger.info(
            "Loaded %d skills: %s",
            len(self._skills),
            ", ".join(sorted(self._skills.keys())),
        )
        return list(self._skills.values())

    async def reload_if_changed(self) -> list[SkillInfo]:
        """Check for new/modified/deleted SKILL.md files.

        Only reloads files whose mtime has changed since last load.
        Deleted files are removed from the skill set.

        Returns:
            List of skills that changed (added or updated).
        """
        changed: list[SkillInfo] = []

        # Check modified and new files
        new_skills = await discover_skills(self._cwd)
        new_map = {s.name: s for s in new_skills}

        # Detect additions and modifications
        for name, skill in new_map.items():
            try:
                current_mtime = Path(skill.path).stat().st_mtime
            except OSError:
                current_mtime = 0.0

            cached_mtime = self._mtime_cache.get(skill.path)
            if cached_mtime is None or current_mtime != cached_mtime:
                changed.append(skill)
                logger.info("Skill '%s' changed, reloading from %s", name, skill.path)

        # Detect deletions
        for old_name in list(self._skills.keys()):
            if old_name not in new_map:
                old_skill = self._skills.pop(old_name)
                self._mtime_cache.pop(old_skill.path, None)
                logger.info("Skill '%s' removed (file deleted)", old_name)

        # Apply updates
        for skill in changed:
            self._skills[skill.name] = skill
            try:
                self._mtime_cache[skill.path] = Path(skill.path).stat().st_mtime
            except OSError:
                self._mtime_cache[skill.path] = 0.0

        return changed

    # ── Lookup ─────────────────────────────────────────────────────────────

    def get(self, name: str) -> SkillInfo | None:
        """Look up a skill by name.

        Args:
            name: Skill name (case-sensitive).

        Returns:
            SkillInfo if found, None otherwise.
        """
        return self._skills.get(name)

    @property
    def names(self) -> set[str]:
        """Set of all loaded skill names."""
        return set(self._skills.keys())

    @property
    def all(self) -> list[SkillInfo]:
        """All loaded skills."""
        return list(self._skills.values())

    @property
    def count(self) -> int:
        """Number of loaded skills."""
        return len(self._skills)

    # ── Formatting ─────────────────────────────────────────────────────────

    def format_available_skills(self) -> str:
        """Generate <available_skills> block for system prompt injection.

        Returns:
            Formatted XML block listing all available skills.
            Returns empty string if no skills are loaded.

        Format::

            <available_skills>
            Each skill is listed with name, description, and trigger hint.
            </available_skills>
        """
        if not self._skills:
            return ""

        lines = [
            "<available_skills>",
            "The following user-defined skills are available. "
            "To invoke a skill, call the 'skill' tool with the skill name "
            "and optional arguments.",
        ]

        for name, skill in sorted(self._skills.items()):
            source = f"[{skill.scope}]" if skill.scope else ""
            lines.append("")
            lines.append(f"### {name} {source}")
            lines.append(f"  Description: {skill.description}")
            if skill.when_to_use:
                lines.append(f"  When to use: {skill.when_to_use}")

        lines.append("")
        lines.append("</available_skills>")
        return "\n".join(lines)

    def format_skill_invocation(self, skill: SkillInfo) -> str:
        """Format a skill as XML for model execution.

        The model follows the skill's instructions as if they were
        additional system instructions scoped to this turn.

        Args:
            skill: The SkillInfo to format.

        Returns:
            XML-wrapped skill body.

        Format::

            <skill name="commit" description="Create git commits" path="/path/to/SKILL.md">
            # Instructions
            1. ...
            </skill>
        """
        return (
            f'<skill name="{skill.name}"'
            f' description="{skill.description}"'
            f' path="{skill.path}">\n'
            f"{skill.body}\n"
            f"</skill>"
        )
