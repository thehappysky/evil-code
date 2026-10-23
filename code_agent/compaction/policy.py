"""Compaction trigger policy.

References:
    - Rust auto-compact threshold logic

Design:
    - context_window default (200k) is a fallback value
    - Real usage should fetch from model metadata (MODEL_CONTEXT_WINDOWS or API)
    - If dynamic fetch fails, degrade to 200k with warning
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class CompactionPolicy:
    """Trigger policy for automatic dialog compaction.

    Parameters:
        context_window: LLM context window size in tokens (default 200k fallback).
        threshold_percent: Trigger percentage (default 85).
        min_tokens: Minimum tokens before even considering compaction (default 5000).

    Usage::

        policy = CompactionPolicy(context_window=200_000)
        if policy.should_compact(session.estimated_tokens):
            # trigger compaction
            target = policy.target_budget()
    """

    def __init__(
        self,
        context_window: int = 200_000,
        threshold_percent: int = 85,
        min_tokens: int = 5000,
    ):
        self._context_window = context_window
        self._threshold_percent = threshold_percent
        self._min_tokens = min_tokens

    @property
    def context_window(self) -> int:
        """Current context window size."""
        return self._context_window

    def should_compact(self, estimated_tokens: int) -> bool:
        """Check if compaction should be triggered.

        Returns True when:
        1. estimated_tokens >= min_tokens (avoid compacting tiny conversations)
        2. estimated_tokens >= context_window * threshold_percent / 100

        Args:
            estimated_tokens: Current estimated token count.

        Returns:
            True if compaction is recommended.
        """
        if estimated_tokens < self._min_tokens:
            return False

        threshold = self._context_window * self._threshold_percent // 100
        result = estimated_tokens >= threshold

        if result:
            logger.info(
                "Compaction triggered: %d tokens >= %d threshold "
                "(%d%% of %d window)",
                estimated_tokens, threshold,
                self._threshold_percent, self._context_window,
            )

        return result

    def target_budget(self) -> int:
        """Post-compaction target token count.

        Returns context_window * 50% -- leaves room for new messages
        and system-reminder injection.

        Returns:
            Target token budget after compaction.
        """
        return self._context_window * 50 // 100

    def update_context_window(self, new_window: int) -> None:
        """Update the context window size (e.g., from model metadata).

        Args:
            new_window: New context window size in tokens.
        """
        if new_window != self._context_window:
            logger.info(
                "Context window updated: %d -> %d",
                self._context_window, new_window,
            )
            self._context_window = new_window
