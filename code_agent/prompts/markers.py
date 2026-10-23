# [TOOL-03] 2026-08-09: Extracted from tools/builtin/web_fetch.py.
# Prompt-injection defense markers belong in the 指令层 (prompts/), not
# inlined in individual tools. Other tools (web_search, etc.) can reuse
# ``wrap_untrusted`` to mark fetched/external content as data, not instructions.
"""Prompt-injection defense markers for untrusted external content.

External content (web pages, search results, tool outputs from untrusted
sources) is wrapped in a marker tag so the model treats it as *data* to
analyze, never as commands to obey.
"""

from __future__ import annotations


def wrap_untrusted(
    content: str,
    source: str,
    kind: str = "web_content",
) -> str:
    """Wrap untrusted content in a marker tag.

    The tag carries ``kind="untrusted-data"`` so the model knows to treat
    the enclosed text as content, not instructions. This is a prompt-level
    defense against prompt-injection from external sources.

    Args:
        content: The untrusted text to wrap.
        source: Origin URL or identifier of the content (for traceability).
        kind: Tag name describing the content type (default ``web_content``).

    Returns:
        The content wrapped in ``<{kind} source="..." kind="untrusted-data">``.
    """
    return f'<{kind} source="{source}" kind="untrusted-data">\n{content}\n</{kind}>'
