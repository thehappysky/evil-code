# [PRM-01] 2026-08-09: Moved from code_agent/rules.py → code_agent/prompts/rules.py.
# Project-rules discovery belongs in the prompts/ package (System Prompt / 指令层).
# [Phase 5] 2026-08-05: Project rules discovery (AGENTS.md / CLAUDE.md)
"""Project rules auto-discovery.

The 2026 fact standard for coding agents is a project rules file that the
agent reads at session start and treats as instructions for this workspace:
    - AGENTS.md (OpenAI Codex convention)
    - CLAUDE.md (Claude Code convention)

Evil discovers whichever file exists (AGENTS.md preferred) and injects its
content as a ``<project-context>`` synthetic user message so the model sees
the rules before it starts working, without relying on memory.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_RULE_FILES = ("AGENTS.md", "CLAUDE.md")


def discover_project_rules(cwd: str | Path) -> str | None:
    """Read the project rules file from ``cwd`` if one exists.

    Checks ``AGENTS.md`` first, then ``CLAUDE.md``. Returns the trimmed file
    content, or None when no rules file is present/readable.

    Args:
        cwd: Project working directory.

    Returns:
        The rules text, or None.
    """
    base = Path(cwd)
    for name in _PROJECT_RULE_FILES:
        path = base / name
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    logger.info("Loaded project rules from %s", path)
                    return text
        except OSError:
            continue
    return None


def format_project_context(rules: str) -> str:
    """Wrap project rules in a ``<project-context>`` block for the model."""
    return f"<project-context>\n{rules}\n</project-context>"
