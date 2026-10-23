# [INT-07] 2026-08-09: Sub-agent capability classification.
# Moved from cli/assembly.py. The capability frozensets and the
# mode→toolset mapping describe sub-agent tool-gating policy and belong
# with the subagents package, not in the CLI assembly root.
"""Sub-agent capability classification.

Defines which built-in tools each sub-agent capability mode is allowed
to use. Used by ``build_tools(capability_mode=...)`` to filter a
sub-agent's tool registry down to its capability set.

Modes:
    - ``read-only``   → read tools only (file_read, file_search,
                        file_glob, web_fetch)
    - ``read-write``  → read + write tools (adds file_edit, file_write,
                        file_delete)
    - ``execute``     → read-write + shell_exec
    - ``all`` / None  → keep every registered tool
"""

from __future__ import annotations


# Read-only tools: pure inspection, no mutation.
_READ_ONLY_TOOLS = frozenset({"file_read", "file_search", "file_glob", "web_fetch"})

# Read-write tools: add file mutation tools.
_READ_WRITE_TOOLS = _READ_ONLY_TOOLS | {"file_edit", "file_write", "file_delete"}

# Execute tools: add shell command execution.
_EXECUTE_TOOLS = _READ_WRITE_TOOLS | {"shell_exec"}


def capability_tool_names(mode: str) -> frozenset | None:
    """Map a sub-agent capability mode to its allowed tool names.

    Args:
        mode: One of "read-only" / "read-write" / "execute" / "all"
              Unknown modes raise ValueError.

    Returns:
        The frozenset of allowed tool names for the mode, or ``None``
        for "all" → caller keeps every registered tool.
    """
    if mode == "read-only":
        return _READ_ONLY_TOOLS
    if mode == "read-write":
        return _READ_WRITE_TOOLS
    if mode == "execute":
        return _EXECUTE_TOOLS
    if mode == "all":
        return None
    raise ValueError(f"Unknown sub-agent capability mode: {mode!r}")


__all__ = [
    "capability_tool_names",
    "_READ_ONLY_TOOLS",
    "_READ_WRITE_TOOLS",
    "_EXECUTE_TOOLS",
]
