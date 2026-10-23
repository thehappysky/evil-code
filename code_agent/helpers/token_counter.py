"""Token estimation utilities.

Logic reference: Rust version xai-token-estimation crate.
Pure functions, zero project-internal dependencies.
"""


def estimate_tokens(text: str) -> int:
    """Estimate token count using bytes/4 heuristic.

    This is a rough approximation: 1 token ~ 4 bytes of UTF-8 text.
    Accurate enough for threshold decisions (compaction trigger, truncation).
    The model returns actual token counts in API responses for precise tracking.
    """
    return len(text.encode("utf-8")) // 4


def exceeds_threshold(used: int, window: int, threshold_pct: int = 85) -> bool:
    """Check if token usage exceeds a percentage threshold of the context window.

    Args:
        used: Current estimated token count.
        window: Total context window size in tokens.
        threshold_pct: Percentage threshold (default 85%).

    Returns:
        True if used tokens exceed threshold_pct% of window.
    """
    if window <= 0:
        return False
    return used >= (window * threshold_pct) // 100
