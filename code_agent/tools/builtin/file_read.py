"""file_read tool - Read file contents with line numbers.

Logic reference: Rust version ReadFileTool
(implementations/grok_build/read_file/mod.rs).

Features:
- Binary detection (null byte > 10% in first 8KB -> reject)
- Line number annotation ({line:6d}|{content})
- Truncation with footer hint for continuation
- Path expansion (expanduser + resolve)
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from code_agent.tools.result import ToolResult


class ReadFileInput(BaseModel):
    """Input for file_read tool."""

    file_path: str = Field(description="Path to the file to read")
    offset: int = Field(default=0, description="Line offset to start reading from (0-based)")
    limit: int = Field(default=2000, description="Maximum number of lines to read")


async def file_read(input: ReadFileInput) -> ToolResult:
    """Read a file and return its contents with line numbers.

    Args:
        input: ReadFileInput with file_path, offset, limit.

    Returns:
        ToolResult with numbered lines or error.
    """
    path = Path(input.file_path).expanduser().resolve()

    if not path.exists():
        return ToolResult.error(f"File not found: {path}")

    if not path.is_file():
        return ToolResult.error(f"Not a file: {path} (is a directory?)")

    # Binary detection: check first 8KB for null bytes
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
        null_count = head.count(b"\x00")
        if len(head) > 0 and null_count / len(head) > 0.10:
            return ToolResult.error(
                f"Binary file detected ({null_count} null bytes in first 8KB). "
                f"Cannot display binary content."
            )
    except OSError as exc:
        return ToolResult.error(f"Cannot read file: {exc}")

    # Read text content
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult.error(f"Cannot read file: {exc}")

    lines = text.splitlines()
    total_lines = len(lines)

    # Apply offset and limit
    start = max(0, input.offset)
    end = start + input.limit
    selected = lines[start:end]

    # Format with line numbers
    numbered = []
    for i, line in enumerate(selected, start=start + 1):
        numbered.append(f"{i:6d}|{line}")

    output = "\n".join(numbered)

    # Truncation footer
    if end < total_lines:
        output += (
            f"\n\n[Output truncated - {total_lines} total lines. "
            f"Showing lines {start + 1}-{min(end, total_lines)}. "
            f"Use offset={end} to continue.]"
        )
        return ToolResult.ok(output, truncated=True)

    return ToolResult.ok(output)
