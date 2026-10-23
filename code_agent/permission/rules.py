# [Phase 4] 2026-07-29: Permission rule matcher — glob-based allow/deny rules
"""Permission rule pattern matcher.

References:
    - design-docs/Python-Agent-MVP-Phase4-详细设计.md §2 permission/rules.py

Pattern format: ToolPrefix(match_spec)

Examples:
    Bash(npm*)     → matches bash calls where command starts with "npm"
    Edit(**/*.py)  → matches file_edit/file_write where file_path ends with ".py"
    WebFetch(domain:github.com) → matches web_fetch where URL domain is github.com
    Bash           → matches any bash call (bare prefix, no parentheses)

Uses fnmatch for glob matching.
"""

from __future__ import annotations

import fnmatch
import re
from urllib.parse import urlparse


def match_rule(pattern: str, tool_name: str, arguments: dict) -> bool:
    """Match a permission rule pattern against a tool call.

    Args:
        pattern: Rule pattern (e.g., "Bash(npm*)", "Edit(**/*.py)").
        tool_name: Name of the tool being called.
        arguments: Tool arguments dict.

    Returns:
        True if the rule pattern matches the tool call.
    """
    # Step 1: Extract tool prefix and match spec
    tool_prefix, match_spec = _parse_pattern(pattern)

    # Step 2: Check tool name matches prefix
    if not _tool_name_matches(tool_prefix, tool_name):
        return False

    # Step 3: If no match spec, any call to this tool matches
    if match_spec is None:
        return True

    # Step 4: Match against the relevant argument field
    return _match_spec(tool_name, match_spec, arguments)


# ── Internal helpers ────────────────────────────────────────────────────────


def _parse_pattern(pattern: str) -> tuple[str, str | None]:
    """Parse a rule pattern into (tool_prefix, match_spec).

    Examples:
        "Bash(npm*)" → ("bash", "npm*")
        "Edit(**/*.py)" → ("edit", "**/*.py")
        "Bash" → ("bash", None)
    """
    # Single token patterns (no parentheses)
    if "(" not in pattern or not pattern.endswith(")"):
        return pattern.lower().strip(), None

    # Pattern with match spec: ToolPrefix(spec)
    paren_idx = pattern.index("(")
    tool_prefix = pattern[:paren_idx].lower().strip()
    match_spec = pattern[paren_idx + 1 : -1].strip()
    return tool_prefix, match_spec


def _tool_name_matches(prefix: str, tool_name: str) -> bool:
    """Check if tool_name matches the rule prefix.

    Args:
        prefix: Rule tool prefix (e.g., "bash", "edit").
        tool_name: Actual tool name (e.g., "bash", "file_edit").

    Returns:
        True if the tool name is covered by the prefix.
    """
    # Direct match
    if prefix == tool_name.lower():
        return True

    # Alias mappings: common shorthand → actual tool names
    ALIAS_MAP: dict[str, list[str]] = {
        "bash": ["bash", "shell_exec"],
        "edit": ["file_edit", "file_write", "search_replace", "write_file"],
        "read": ["file_read", "file_search", "file_grep"],
        "write": ["file_write", "file_edit"],
        "web": ["web_fetch", "web_search"],
        "webfetch": ["web_fetch"],
    }

    if prefix in ALIAS_MAP:
        return tool_name.lower() in ALIAS_MAP[prefix]

    return False


def _match_spec(tool_name: str, spec: str, arguments: dict) -> bool:
    """Match a rule spec against tool arguments.

    Args:
        tool_name: Tool name (determines which argument field to check).
        spec: Match specification string.
        arguments: Tool arguments dict.

    Returns:
        True if the spec matches the relevant argument.
    """
    # For shell/command tools: match against "command" argument
    if tool_name in ("bash", "shell_exec"):
        command = str(arguments.get("command", ""))
        return _glob_match(spec, command)

    # For file tools: match against "file_path" (or "path" for file_search).
    # file_search uses the key "path", so a rule like Read(**/*.log) was
    # silently ineffective against it before.
    if tool_name in ("file_edit", "file_write", "file_delete", "file_read", "file_search",
                     "search_replace", "write_file"):
        file_path = str(arguments.get("file_path") or arguments.get("path") or "")
        return _glob_match(spec, file_path)

    # For web tools: match against "url" argument
    if tool_name in ("web_fetch", "web_search", "WebFetch"):
        url = str(arguments.get("url", ""))
        if spec.startswith("domain:"):
            domain = spec[len("domain:"):]
            return _domain_matches(domain, url)
        return _glob_match(spec, url)

    # Generic: try "command" then "file_path" then "url"
    for key in ("command", "file_path", "url"):
        val = str(arguments.get(key, ""))
        if val:
            return _glob_match(spec, val)

    return False


def _glob_match(pattern: str, text: str) -> bool:
    """Match a glob pattern against text.

    Uses fnmatch for *, **, ? support.
    Pattern is treated case-insensitively.
    Also tries prefix matching when the glob doesn't contain wildcards.
    """
    # Try glob match
    if fnmatch.fnmatch(text.lower(), pattern.lower()):
        return True

    # Also try matching with working-directory prefix for **/ patterns
    if pattern.startswith("**/"):
        simple = pattern[3:]  # Strip **/ prefix
        if fnmatch.fnmatch(text.lower(), simple.lower()):
            return True
        # Try matching against just the filename portion
        import os
        filename = os.path.basename(text)
        if fnmatch.fnmatch(filename.lower(), simple.lower()):
            return True

    # Prefix match for patterns without wildcards
    if "*" not in pattern and "?" not in pattern:
        return text.lower().startswith(pattern.lower())

    return False


def _domain_matches(domain: str, url: str) -> bool:
    """Check if a URL's domain matches.

    Args:
        domain: Expected domain (e.g., "github.com").
        url: Full URL string.

    Returns:
        True if the URL's hostname matches the expected domain.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        # Match exact or subdomain
        return hostname == domain or hostname.endswith("." + domain)
    except Exception:
        return False
