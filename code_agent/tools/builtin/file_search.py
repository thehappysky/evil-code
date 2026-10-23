"""file_search tool - Code search with ripgrep or Python re fallback.

Logic reference: Rust version GrepTool
(implementations/grok_build/grep/mod.rs).

Strategy:
- Prefer system-installed `rg` (structured output, fast)
- Fallback to Python `re` module if rg not available
- Truncation with footer when exceeding max_results
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from code_agent.tools.result import ToolResult


class SearchInput(BaseModel):
    """Input for file_search tool."""

    pattern: str = Field(description="Regex pattern to search for")
    path: str = Field(default=".", description="Directory or file to search in")
    include: str | None = Field(default=None, description="Glob pattern to filter files (e.g. '*.py')")
    ignore_case: bool = Field(default=False, description="Case-insensitive search")
    max_results: int = Field(default=50, description="Maximum number of results to return")


async def file_search(input: SearchInput) -> ToolResult:
    """Search for a pattern in files using ripgrep or Python re.

    Args:
        input: SearchInput with pattern, path, include, ignore_case, max_results.

    Returns:
        ToolResult with matching lines (file:line:content format).
    """
    search_path = Path(input.path).expanduser().resolve()

    if not search_path.exists():
        return ToolResult.error(f"Path not found: {search_path}")

    # Try ripgrep first
    rg_path = shutil.which("rg")
    if rg_path:
        return await _search_with_rg(rg_path, input, search_path)

    # Fallback to Python re
    return await _search_with_python_re(input, search_path)


async def _search_with_rg(
    rg_path: str, input: SearchInput, search_path: Path
) -> ToolResult:
    """Search using ripgrep subprocess."""
    cmd = [rg_path, "--line-number", "--no-heading", "--color=never"]

    if input.ignore_case:
        cmd.append("--ignore-case")

    if input.include:
        cmd.extend(["--glob", input.include])

    cmd.extend(["--max-count", str(input.max_results + 1)])
    cmd.append(input.pattern)
    cmd.append(str(search_path))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    except asyncio.TimeoutError:
        proc.kill()
        return ToolResult.error("Search timed out (30s limit)")
    except OSError as exc:
        return ToolResult.error(f"Failed to run ripgrep: {exc}")

    output = stdout.decode("utf-8", errors="replace").strip()

    if not output:
        return ToolResult.ok("No matches found.")

    lines = output.splitlines()
    truncated = len(lines) > input.max_results

    if truncated:
        lines = lines[: input.max_results]
        output = "\n".join(lines)
        output += f"\n\n[Results truncated - showing first {input.max_results} matches.]"
        return ToolResult.ok(output, truncated=True)

    return ToolResult.ok(output)


async def _search_with_python_re(input: SearchInput, search_path: Path) -> ToolResult:
    """Fallback search using Python re module."""
    flags = re.IGNORECASE if input.ignore_case else 0
    try:
        pattern = re.compile(input.pattern, flags)
    except re.error as exc:
        return ToolResult.error(f"Invalid regex pattern: {exc}")

    results: list[str] = []
    files_searched = 0

    # Determine files to search
    if search_path.is_file():
        files = [search_path]
    else:
        glob_pat = input.include or "*"
        files = sorted(search_path.rglob(glob_pat))

    for file_path in files:
        if not file_path.is_file():
            continue

        # Skip binary files (quick check)
        try:
            with open(file_path, "rb") as f:
                head = f.read(1024)
            if b"\x00" in head:
                continue
        except OSError:
            continue

        files_searched += 1

        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for line_no, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                # Relative path for readability
                try:
                    rel = file_path.relative_to(search_path)
                except ValueError:
                    rel = file_path
                results.append(f"{rel}:{line_no}:{line}")

                if len(results) > input.max_results:
                    output = "\n".join(results[: input.max_results])
                    output += (
                        f"\n\n[Results truncated - showing first {input.max_results} matches. "
                        f"Searched {files_searched} files.]"
                    )
                    return ToolResult.ok(output, truncated=True)

    if not results:
        return ToolResult.ok(f"No matches found (searched {files_searched} files).")

    return ToolResult.ok("\n".join(results))
