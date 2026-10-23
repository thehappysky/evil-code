"""LLM-based conversation summarization.

References:
    - Rust generate_session_compact() (helpers/session_compact.rs)

Simplified input ladder (Phase 2):
    1. Fit: trim conversation to context_window * 70% - tool_tokens
    2. Send to LLM with summarization prompt
    3. Return cleaned summary text (strip scratchpad/analysis blocks)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from code_agent.helpers.resilience import RetryConfig
from code_agent.helpers.token_counter import estimate_tokens

logger = logging.getLogger(__name__)

# Tokens reserved for summarization prompt itself
_SUMMARIZE_PROMPT_TOKENS = 200

# [Phase 3] 2026-08-04: backoff between transient-error retries
_RETRY_BACKOFF = RetryConfig(max_retries=1, initial_delay=0.5, jitter_factor=0.0)

# [Phase 5] 2026-08-05: summarizer system prompt centralized (grok-build adapted).
from code_agent.prompts import SUMMARIZE_SYSTEM_PROMPT as _SUMMARIZE_SYSTEM_PROMPT


class SummaryClient(Protocol):
    """Minimal protocol for LLM client used by summarizer.

    Only requires a single async method — the summarizer doesn't need
    streaming, tool calls, or any other ChatClient features.
    """

    async def chat(
        self, messages: list[dict[str, Any]], max_tokens: int
    ) -> str:
        """Send a chat completion request and return the text response."""
        ...




async def generate_summary(
    client: SummaryClient,
    conversation: list[dict[str, Any]],
    system_prompt: str,
    *,
    context_window: int = 200_000,
) -> str:
    """Summarize conversation using a dedicated LLM call.

    Args:
        client: LLM client implementing SummaryClient protocol.
        conversation: List of API-format message dicts to summarize.
        system_prompt: The agent's system prompt (for context).
        context_window: Model's context window size for fitting.

    Returns:
        Summary text string.

    Raises:
        ValueError: If conversation is empty.
        RuntimeError: If LLM call fails after retries.
    """
    if not conversation:
        raise ValueError("Cannot summarize empty conversation")

    # Step 1: Fit conversation to available budget
    max_input_tokens = int(context_window * 0.70) - _SUMMARIZE_PROMPT_TOKENS
    fitted = _fit_conversation(
        conversation, system_prompt, max_input_tokens
    )

    # Step 2: Build summarization request
    messages = [
        {"role": "system", "content": _SUMMARIZE_SYSTEM_PROMPT},
        {"role": "user", "content": _build_summarize_user_message(fitted)},
    ]

    # Step 3: Call LLM (single retry with backoff on transient errors)
    for attempt in range(2):
        try:
            summary = await client.chat(messages, max_tokens=2048)
            # Step 4: Clean summary
            return _clean_summary(summary)
        except Exception as exc:
            error_type = classify_compact_error(exc)
            if error_type == "deterministic" or attempt == 1:
                logger.error(
                    "Compaction summary failed (%s, attempt %d/2): %s",
                    error_type, attempt + 1, exc,
                )
                raise RuntimeError(
                    f"Failed to generate compaction summary: {exc}"
                ) from exc
            logger.warning(
                "Compaction summary transient error, retrying: %s", exc
            )
            # [Phase 3] 2026-08-04: back off before the retry instead of
            # hammering the provider immediately.
            await asyncio.sleep(_RETRY_BACKOFF.delay_for(attempt))

    # Unreachable, but type checker needs it
    raise RuntimeError("Failed to generate compaction summary")


def classify_compact_error(error: Exception) -> str:
    """Classify compaction failure for retry logic.

    Returns:
        "deterministic" — no retry (auth, config, schema issues)
        "transient" — retry with backoff (network, server errors)
    """
    import httpx

    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status in (401, 403):
            return "deterministic"  # Auth error
        if 400 <= status < 500:
            return "deterministic"  # Bad request / config
        if 500 <= status < 600:
            return "transient"  # Server error

    if isinstance(error, (httpx.TimeoutException, httpx.ConnectError)):
        return "transient"

    if isinstance(error, (ValueError, TypeError)):
        return "deterministic"  # Schema/data issues

    # Unknown errors: treat as deterministic (don't retry)
    return "deterministic"


def _fit_conversation(
    conversation: list[dict[str, Any]],
    system_prompt: str,
    max_input_tokens: int,
) -> list[dict[str, Any]]:
    """Trim conversation to fit within max_input_tokens.

    Strategy:
    1. Always keep the system prompt (or first system message)
    2. Always keep the last user message (current request)
    3. Keep as many prefix messages as fit within budget
    """
    if not conversation:
        return []

    budget = max_input_tokens

    # Reserve for system prompt
    sys_tokens = estimate_tokens(system_prompt)
    budget -= sys_tokens

    # Reserve for last message (current user request)
    last_msg = conversation[-1]
    last_tokens = estimate_tokens(str(last_msg.get("content", "")))
    budget -= last_tokens

    if budget <= 0:
        # Very tight budget: return system + last message only
        logger.warning(
            "Tight summary budget: sys=%d, last=%d, max=%d. "
            "Truncating to minimal.",
            sys_tokens, last_tokens, max_input_tokens,
        )
        return [{"role": "system", "content": system_prompt}, last_msg]

    # Collect messages from the start until budget is exhausted
    fitted: list[dict[str, Any]] = []
    remaining = budget

    for msg in conversation[:-1]:  # Exclude last message (appended separately)
        msg_tokens = estimate_tokens(str(msg.get("content", "")))
        if remaining - msg_tokens >= 0:
            fitted.append(msg)
            remaining -= msg_tokens
        else:
            break

    # Always append the last message
    fitted.append(last_msg)

    logger.info(
        "Conversation fitted: %d/%d messages (%d tokens budget)",
        len(fitted), len(conversation), max_input_tokens,
    )

    return fitted


def _build_summarize_user_message(conversation: list[dict[str, Any]]) -> str:
    """Build the user message asking for summarization."""
    lines = [
        "Please summarize the following conversation between a user "
        "and an AI coding agent:",
        "",
    ]
    for msg in conversation:
        role = msg.get("role", "unknown")
        content = str(msg.get("content", ""))[:2000]  # Truncate per message
        lines.append(f"[{role}]: {content}")
        lines.append("")

    lines.append("Provide your summary now:")
    return "\n".join(lines)


def _clean_summary(summary: str) -> str:
    """Clean LLM-generated summary text.

    Removes common LLM artifacts:
    - "Here is a summary:" prefixes
    - Scratchpad/analysis blocks
    - Meta-commentary
    """
    # Strip common prefixes
    prefixes = [
        "Here is a summary of the conversation:",
        "Here is the summary:",
        "Here's a summary of the conversation:",
        "Here's the summary:",
        "Summary:",
    ]
    for prefix in prefixes:
        if summary.lower().startswith(prefix.lower()):
            summary = summary[len(prefix):].strip()

    # Remove markdown code fences if the entire output is wrapped
    if summary.startswith("```") and summary.endswith("```"):
        # Strip the outermost code fence
        idx = summary.index("\n")
        end_idx = summary.rindex("```")
        summary = summary[idx + 1:end_idx].strip()

    return summary.strip()
