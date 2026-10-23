"""file_glob tool - List files matching a glob pattern.

References:
    - Rust GlobTool (implementations/grok_build/glob/mod.rs)
    - design-docs/Python-Agent-补足方案-设计.md §Phase 4

Strategy:
    - Prefer system-installed `rg` (--files) which respects .gitignore,
      skips .git, and includes hidden files (--hidden).
    - Fall back to a pure-Python walk (pathlib + fnmatch) when rg is absent.
    - Results sorted by most-recently-modified, capped at RESULT_LIMIT, with
      a truncation footer so the model knows the list is incomplete.
"""

from __future__ import annotations

import asyncio
import fnmatch
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from code_agent.tools.result import ToolResult

_RESULT_LIMIT = 100
_TIMEOUT = 30.0


class GlobInput(BaseModel):
    """Input for the file_glob tool."""

    pattern: str = Field(
        description=(
            'Glob pattern to match files, e.g. "**/*.py" or "src/**/*.tsx". '
            "** crosses directories; * matches within one segment."
        ),
    )
    path: str | None = Field(
        default=None,
        description="Directory to search in (default: current working directory).",
    )


async def file_glob(input: GlobInput) -> ToolResult:
    """List file paths matching a glob pattern.

    Args:
        input: GlobInput with pattern and optional path.

    Returns:
        ToolResult with one absolute/relative path per line (mtime-sorted),
        or an error ToolResult.
    """
    search_path = Path(input.path).expanduser().resolve() if input.path else Path.cwd()

    if not search_path.exists():
        return ToolResult.error(f"Path not found: {search_path}")
    if not search_path.is_dir():
        return ToolResult.error(f"Not a directory: {search_path}")

    rg = shutil.which("rg")
    if rg:
        matches = await _glob_with_rg(rg, input, search_path)
    else:
        matches = _glob_with_python(input, search_path)

    return _format(matches, search_path)


# ── rg backend ────────────────────────────────────────────────────────────────


async def _glob_with_rg(
    rg: str, input: GlobInput, search_path: Path,
) -> list[Path]:
    """Use ``rg --files`` which respects .gitignore and is fast."""
    cmd = [
        rg, "--files", "--hidden",
        "--glob=!.git/**",
        "--glob", input.pattern,
        str(search_path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_TIMEOUT,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return []
    except OSError:
        return []

    return [Path(line) for line in stdout.decode("utf-8", errors="replace").splitlines() if line]


# ── Python fallback ───────────────────────────────────────────────────────────


def _glob_with_python(input: GlobInput, search_path: Path) -> list[Path]:
    """Walk the tree and match relative paths with fnmatch.

    ``**`` is folded into ``*`` so fnmatch can cross directory separators —
    a close enough approximation for a fallback backend.
    """
    fn = input.pattern.replace("**", "*")
    matches: list[Path] = []
    for candidate in search_path.rglob("*"):
        if not candidate.is_file():
            continue
        try:
            rel = candidate.relative_to(search_path)
        except ValueError:
            rel = candidate
        rel_str = str(rel).replace("\\", "/")
        # Skip .git internals explicitly (rglob has no ignore semantics).
        if "/.git/" in rel_str or rel_str.startswith(".git/"):
            continue
        if fnmatch.fnmatch(rel_str, fn) or fnmatch.fnmatch(rel_str, fn.lstrip("./")):
            matches.append(candidate)
    return matches


# ── Formatting ────────────────────────────────────────────────────────────────


def _format(matches: list[Path], search_path: Path) -> ToolResult:
    """Sort by mtime (desc) and format one path per line, capped + footered."""
    if not matches:
        return ToolResult.ok("No files found.")

    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    matches.sort(key=_mtime, reverse=True)

    def _display(p: Path) -> str:
        try:
            rel = str(p.relative_to(search_path))
        except ValueError:
            rel = str(p)
        return rel.replace("\\", "/")

    truncated = len(matches) > _RESULT_LIMIT
    shown = matches[:_RESULT_LIMIT]
    lines = [_display(p) for p in shown]

    if truncated:
        lines.append(
            f"\n[Results truncated - showing first {_RESULT_LIMIT} matches "
            f"out of {len(matches)}. Narrow the pattern to see more.]"
        )
        return ToolResult.ok("\n".join(lines), truncated=True)

    return ToolResult.ok("\n".join(lines))
