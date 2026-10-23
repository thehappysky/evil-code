"""file_edit tool (search_replace) - Precise string replacement in files.

Logic reference: Rust version SearchReplaceTool
(implementations/grok_build/search_replace/mod.rs).

Features:
- Unique match check: replace_all=False requires exactly one occurrence
- Atomic write: tempfile + os.replace (no half-written files on interrupt)
- Diff output: difflib.unified_diff for model feedback
- Large file protection: >1MB rejected
- Stateless: no "read first" enforcement at tool level; match failure
  naturally guides model to read_file first
"""

from __future__ import annotations

import difflib
from pathlib import Path

from pydantic import BaseModel, Field

from code_agent.helpers.file_io import atomic_write
from code_agent.tools.result import ToolResult

_MAX_FILE_SIZE = 1_000_000  # 1MB


class SearchReplaceInput(BaseModel):
    """Input for file_edit (search_replace) tool."""

    file_path: str = Field(description="Path to the file to edit")
    old_string: str = Field(description="Exact text to find and replace")
    new_string: str = Field(description="Replacement text")
    replace_all: bool = Field(default=False, description="Replace all occurrences (default: exactly one match required)")


async def file_edit(input: SearchReplaceInput) -> ToolResult:
    """Perform precise string replacement in a file.

    Args:
        input: SearchReplaceInput with file_path, old_string, new_string, replace_all.

    Returns:
        ToolResult with unified diff on success, or specific error message.
    """
    path = Path(input.file_path).expanduser().resolve()

    # Validation
    if not path.exists():
        return ToolResult.error(f"File not found: {path}")

    if not path.is_file():
        return ToolResult.error(f"Not a file: {path}")

    if input.old_string == input.new_string:
        return ToolResult.error("old_string and new_string are identical — no change needed.")

    if not input.old_string:
        return ToolResult.error("old_string cannot be empty.")

    # Large file protection
    file_size = path.stat().st_size
    if file_size > _MAX_FILE_SIZE:
        return ToolResult.error(
            f"File too large ({file_size:,} bytes > 1MB limit). "
            f"Use shell_exec with sed for large file edits."
        )

    # Read file
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ToolResult.error(f"Cannot read file: {exc}")

    # Count occurrences
    count = content.count(input.old_string)

    if count == 0:
        return ToolResult.error(
            f"No match found for old_string in {path.name}.\n"
            f"Suggestion: use file_read to verify the exact content first.\n"
            f"Searched for: {input.old_string[:200]}{'...' if len(input.old_string) > 200 else ''}"
        )

    if count > 1 and not input.replace_all:
        return ToolResult.error(
            f"Found {count} occurrences of old_string (expected exactly 1).\n"
            f"Provide more context in old_string to make it unique, "
            f"or set replace_all=true to replace all."
        )

    # Perform replacement
    if input.replace_all:
        new_content = content.replace(input.old_string, input.new_string)
    else:
        new_content = content.replace(input.old_string, input.new_string, 1)

    # Atomic write
    try:
        atomic_write(path, new_content)
    except OSError as exc:
        return ToolResult.error(f"Failed to write file: {exc}")

    # Generate diff for model feedback
    old_lines = content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{path.name}",
        tofile=f"b/{path.name}",
        lineterm="",
    )
    diff_text = "".join(diff)

    if not diff_text:
        diff_text = "(no visible diff — whitespace-only change?)"

    replacements = count if input.replace_all else 1
    summary = f"Replaced {replacements} occurrence(s) in {path.name}.\n\n{diff_text}"

    return ToolResult.ok(summary)
