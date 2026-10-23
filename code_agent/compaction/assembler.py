"""Compacted history assembly.

References:
    - Rust build_compacted_history() (compaction_utils.rs:828)

Result structure:
    [system_message, user(project_rules), user(summary), ...recent_messages, user(reminders)]

The summary is injected as a synthetic user message so the model sees it
as context rather than instruction. Recent messages (last N turns) are
preserved verbatim for immediate context continuity.
"""

from __future__ import annotations

from typing import Any


def assemble_compacted_history(
    system_prompt: str,
    summary: str,
    recent_messages: list[dict[str, Any]],
    reminders: str | None = None,
    *,
    project_rules: str | None = None,
) -> list[dict[str, Any]]:
    """Assemble post-compaction conversation history.

    Args:
        system_prompt: The agent's system prompt.
        summary: Generated conversation summary text.
        recent_messages: Last N turns of conversation to preserve verbatim.
        reminders: Optional system-reminder block text (e.g., todo, memory context).
        project_rules: Optional project rules (AGENTS.md / CLAUDE.md content).

    Returns:
        List of message dicts in OpenAI API format, ready for the next turn.

    Structure::

        [system(prompt), user(rules)?, user(summary), ...recent_msgs, user(reminders)?]
    """
    assembled: list[dict[str, Any]] = []

    # 1. System prompt
    assembled.append({"role": "system", "content": system_prompt})

    # 2. Project rules (if any) — injected as user message for visibility
    if project_rules:
        assembled.append(
            {
                "role": "user",
                "content": f"<project-context>\n{project_rules}\n</project-context>",
            }
        )

    # 3. Summary as synthetic user message
    assembled.append(
        {
            "role": "user",
            "content": (
                "<conversation-history-summary>\n"
                "The following is a summary of the conversation so far. "
                "Use this to get up to speed and continue helping the user. "
                "Some contents may be omitted, indicated by '<omitted />'.\n\n"
                f"{summary}\n"
                "</conversation-history-summary>"
            ),
        }
    )

    # 4. Recent messages preserved verbatim
    assembled.extend(recent_messages)

    # 5. Reminders (if any)
    if reminders:
        assembled.append({"role": "user", "content": reminders})

    return assembled


def count_preserved_turns(
    all_messages: list[dict[str, Any]],
    max_recent_tokens: int,
    token_estimator: callable = len,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split messages into to-summarize and to-preserve.

    Preserves the last N user-assistant exchange pairs that fit within
    max_recent_tokens. All older messages go into the summarize pile.

    The split boundary is then snapped to a safe point (Rust
    ``snap_to_safe_boundary`` in ``xai-grok-compaction/select.rs``): the
    preserved window never starts on a bare tool result, otherwise the model
    API would receive an orphan ``tool`` message whose owning assistant was
    summarized away (HTTP 400).

    Args:
        all_messages: Full conversation in API format.
        max_recent_tokens: Maximum tokens to preserve in recent window.
        token_estimator: Function to estimate tokens (default: len as char count).

    Returns:
        Tuple of (to_summarize, to_preserve).
    """
    if not all_messages:
        return [], []

    # Walk backwards and accumulate complete turns
    preserved: list[dict[str, Any]] = []
    used = 0

    for msg in reversed(all_messages):
        # Skip system messages (handled separately)
        if msg.get("role") == "system":
            continue

        msg_tokens = token_estimator(str(msg.get("content", "")))
        if used + msg_tokens <= max_recent_tokens:
            preserved.insert(0, msg)
            used += msg_tokens
        else:
            break

    # Snap the split boundary to a safe point so it never starts on a
    # bare tool result (orphan). This may slightly exceed the token budget —
    # that is intentional and matches the Rust reference. Only when something
    # was preserved (start_idx < len) is there a boundary to snap.
    preserve_count = len(preserved)
    if preserve_count > 0:
        start_idx = len(all_messages) - preserve_count
        safe_start = _snap_to_safe_boundary(all_messages, start_idx)
        if safe_start != start_idx:
            preserved = all_messages[safe_start:]
            preserve_count = len(preserved)

    # Everything not preserved goes to summarize
    to_summarize = all_messages[:-preserve_count] if preserve_count > 0 else all_messages

    return to_summarize, preserved


def _snap_to_safe_boundary(
    messages: list[dict[str, Any]], start_idx: int,
) -> int:
    """Move a split boundary backward so it does not open on an orphan tool result.

    Tool results always follow their owning ``assistant`` message (with
    ``tool_calls``). If the boundary points at a ``tool`` message, walk back
    over the whole tool-result run to the owning assistant. Other message
    types (user / text assistant / system) are always safe boundaries.

    Args:
        messages: Full conversation in API format.
        start_idx: Proposed preserve-window start index.

    Returns:
        A safe start index (<= start_idx).
    """
    idx = start_idx
    while idx > 0 and messages[idx].get("role") == "tool":
        idx -= 1
    return idx
