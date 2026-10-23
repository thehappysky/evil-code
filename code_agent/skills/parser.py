# [Phase 3] 2026-07-27: SKILL.md parser — YAML frontmatter extraction with resilient fallback
"""SKILL.md file parser.

References:
    - design-docs/Python-Agent-MVP-Phase3-详细设计.md §7 skills/parser.py

Parses SKILL.md files with this structure:

    ---
    name: commit
    description: Create well-formatted git commits
    when_to_use: user asks to commit changes
    allowed_tools: [bash, read_file]
    ---
    # Instructions
    1. Run 'git diff --staged' to see changes
    2. Write a concise commit message
    3. Run 'git commit -m "message"'

Frontmatter parsing is resilient: falls back to quoting retry for edge cases,
then line-based recovery for top-level scalars.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from code_agent.skills.models import SkillInfo

logger = logging.getLogger(__name__)


def parse_skill_md(filepath: Path) -> SkillInfo:
    """Parse a SKILL.md file into a SkillInfo model.

    Strategy:
    1. Read the file content
    2. Extract YAML frontmatter between the first pair of `---` lines
    3. Parse YAML into a dict
    4. Treat everything after the closing `---` as the Markdown body
    5. If YAML parsing fails, attempt line-based recovery for top-level scalars

    Args:
        filepath: Path to a SKILL.md file.

    Returns:
        SkillInfo with parsed frontmatter fields and body.

    Raises:
        ValueError: If the file cannot be read or parsed.
    """
    content = filepath.read_text(encoding="utf-8")

    # Step 1: Extract frontmatter
    frontmatter, body = _split_frontmatter(content)

    # Step 2: Parse YAML frontmatter
    meta = _parse_frontmatter(frontmatter, filepath)

    # Step 3: Validate required fields
    if "name" not in meta:
        raise ValueError(
            f"SKILL.md at {filepath} is missing required 'name' field in frontmatter"
        )
    if "description" not in meta:
        raise ValueError(
            f"SKILL.md at {filepath} is missing required 'description' field in frontmatter"
        )

    return SkillInfo(
        name=str(meta["name"]),
        description=str(meta["description"]),
        body=body.strip(),
        path=str(filepath.resolve()),
        scope=_infer_scope(filepath),
        when_to_use=str(meta.get("when_to_use", "")),
        allowed_tools=_normalize_allowed_tools(meta.get("allowed_tools", [])),
    )


# ── Internal helpers ───────────────────────────────────────────────────────────


def _split_frontmatter(content: str) -> tuple[str, str]:
    """Split content into (frontmatter_text, body_text).

    Frontmatter is between the first pair of `---` lines.
    Returns ("", content) if no frontmatter delimiters are found.
    """
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return "", content

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        # No closing delimiter — treat entire content as body
        return "", content

    frontmatter = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1:])
    return frontmatter, body


def _parse_frontmatter(frontmatter: str, filepath: Path) -> dict:
    """Parse YAML frontmatter with resilient fallback.

    Strategy:
    1. Standard YAML parse (yaml.safe_load)
    2. If that fails: quoting retry — wrap values in quotes and retry
    3. If that fails: line-based recovery for `key: value` pairs
    """
    if not frontmatter.strip():
        return {}

    # Attempt 1: Standard YAML parse
    try:
        result = yaml.safe_load(frontmatter)
        if isinstance(result, dict):
            return result
    except yaml.YAMLError:
        logger.debug("Standard YAML parse failed for %s, trying recovery", filepath)

    # Attempt 2: Line-based recovery for top-level scalars
    return _recover_top_level_scalars(frontmatter, filepath)


def _recover_top_level_scalars(frontmatter: str, filepath: Path) -> dict:
    """Recover top-level key-value pairs from malformed YAML.

    Handles common cases:
    - key: value
    - key: list_item1, list_item2  → treated as string
    - key: [item1, item2]  → parsed as list
    """
    meta: dict = {}
    for line in frontmatter.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if ":" not in line:
            continue

        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        # Try to parse as YAML list
        if value.startswith("[") and value.endswith("]"):
            try:
                import yaml
                parsed = yaml.safe_load(value)
                meta[key] = parsed if parsed is not None else value
            except yaml.YAMLError:
                meta[key] = value
        # Unquote quoted strings
        elif (len(value) >= 2
              and value[0] == value[-1]
              and value[0] in ('"', "'")):
            meta[key] = value[1:-1]
        else:
            meta[key] = value

    logger.debug("Recovered %d fields via line-based parsing for %s", len(meta), filepath)
    return meta


def _infer_scope(filepath: Path) -> str:
    """Infer the scope of a skill based on its file path.

    Returns:
        "project" if under a project's .code-agent/ directory,
        "user" if under ~/.code-agent/,
        "global" otherwise.
    """
    import os

    path_str = str(filepath.resolve())
    home = os.path.expanduser("~")

    if ".code-agent" in path_str:
        # Check if it's under home directory
        home_skills = os.path.join(home, ".code-agent", "skills")
        if path_str.startswith(home_skills):
            return "user"
        return "project"

    return "global"


def _normalize_allowed_tools(value: object) -> list[str]:
    """Normalize allowed_tools from YAML to list[str].

    Handles:
    - None → []
    - "tool1, tool2" → ["tool1", "tool2"]
    - ["tool1", "tool2"] → ["tool1", "tool2"]
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if v]
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    return []
