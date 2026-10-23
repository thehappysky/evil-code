"""file_write tool (write_file) - Create new files or overwrite existing ones.

Logic reference: Rust version OpenCodeWriteTool
(implementations/opencode/write/mod.rs).

Features:
- Safety check: reject writes to system directories
- Auto-create parent directories (mkdir -p)
- Overwrite protection: read old content for diff, preserve permissions
- Atomic write: temp file + os.replace
- Large file protection: >5MB rejected
"""

from __future__ import annotations

import difflib
import os
from pathlib import Path

from pydantic import BaseModel, Field

from code_agent.helpers.file_io import atomic_write
from code_agent.tools.result import ToolResult

_MAX_FILE_SIZE = 5_000_000  # 5MB

# Directories that should never be written to
_FORBIDDEN_PREFIXES = (
    "/etc/",
    "/sys/",
    "/proc/",
    "/boot/",
    "~/.ssh/",
    "~/.gnupg/",
)


class WriteFileInput(BaseModel):
    """Input for file_write tool."""

    file_path: str = Field(description="Path to the file to create or overwrite")
    content: str = Field(description="Content to write to the file")


async def file_write(input: WriteFileInput) -> ToolResult:
    """Create a new file or overwrite an existing one.

    Args:
        input: WriteFileInput with file_path and content.

    Returns:
        ToolResult with creation/overwrite confirmation and diff.
    """
    path = Path(input.file_path).expanduser().resolve()

    # Safety check: forbidden directories
    path_str = str(path)
    home = str(Path.home())
    for prefix in _FORBIDDEN_PREFIXES:
        expanded = prefix.replace("~", home)
        if path_str.startswith(expanded) or path_str.startswith(prefix):
            return ToolResult.error(
                f"Refused: cannot write to system directory ({prefix}). "
                f"Path: {path}"
            )

    # Large file protection
    if len(input.content.encode("utf-8")) > _MAX_FILE_SIZE:
        return ToolResult.error(
            f"Content too large (>5MB). Use shell_exec with redirection for large files."
        )

    # Check if overwriting
    is_new = not path.exists()
    old_content: str | None = None
    old_mode: int | None = None

    if not is_new:
        try:
            old_content = path.read_text(encoding="utf-8")
            old_mode = path.stat().st_mode
        except OSError:
            old_content = None

    # Create parent directories
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return ToolResult.error(f"Cannot create directory {path.parent}: {exc}")

    # Atomic write
    try:
        atomic_write(path, input.content)
    except OSError as exc:
        return ToolResult.error(f"Failed to write file: {exc}")

    # Restore permissions if overwriting
    if old_mode is not None:
        try:
            os.chmod(str(path), old_mode)
        except OSError:
            pass

    # Generate feedback
    if is_new:
        line_count = input.content.count("\n") + 1
        return ToolResult.ok(
            f"Created new file: {path}\n"
            f"({line_count} lines, {len(input.content)} characters)"
        )

    # Diff for overwrite
    old_lines = (old_content or "").splitlines(keepends=True)
    new_lines = input.content.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{path.name}",
        tofile=f"b/{path.name}",
        lineterm="",
    )
    diff_text = "".join(diff)

    if diff_text:
        # Truncate very long diffs
        if len(diff_text) > 5000:
            diff_text = diff_text[:5000] + "\n[diff truncated]"
        return ToolResult.ok(f"Overwritten: {path}\n\n{diff_text}")

    return ToolResult.ok(f"Overwritten: {path} (content unchanged)")
