"""Unified tool return type.

Logic reference: Rust version ToolRunResult (types/output.rs).

All tools return ToolResult to the model via this unified type.
- is_error: lets model know result is unusable (may trigger retry)
- truncated: lets model know info is incomplete (may trigger read_file)
"""

from __future__ import annotations

from pydantic import BaseModel


class ToolResult(BaseModel):
    """Unified tool execution result.

    Attributes:
        output: Text shown to the model (may be truncated).
        is_error: Whether execution failed.
        truncated: Whether output was truncated (info incomplete).
    """

    output: str
    is_error: bool = False
    truncated: bool = False
    meta: dict | None = None

    @classmethod
    def ok(cls, text: str, truncated: bool = False) -> ToolResult:
        """Create a successful result."""
        return cls(output=text, is_error=False, truncated=truncated)

    @classmethod
    def error(cls, text: str) -> ToolResult:
        """Create an error result."""
        return cls(output=text, is_error=True, truncated=False)
