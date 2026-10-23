"""Hybrid search pipeline.

References:
    - Rust search.rs hybrid search pipeline

Pipeline:
    1. FTS5 keyword search (always available, LIKE fallback)
    2. Filter empty/template chunks
    3. Rank by source weight: global(1.0) > workspace(0.8) > session(0.5)
    4. Truncate to limit
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from code_agent.memory.index_db import MemoryIndex

logger = logging.getLogger(__name__)

# Source weight multipliers
_SOURCE_WEIGHTS: dict[str, float] = {
    "global": 1.0,
    "workspace": 0.8,
    "session": 0.5,
}

# Template patterns that indicate empty/boilerplate chunks
_EMPTY_TEMPLATES = [
    "<!--",  # HTML comments (timestamps, markers)
]


@dataclass
class SearchResult:
    """A single search result from memory.

    Attributes:
        chunk_id: Unique chunk identifier.
        path: Source file path.
        text: Chunk text content.
        source: Source type ("global", "workspace", "session").
        score: Weighted relevance score (higher = more relevant).
    """

    chunk_id: str
    path: str
    text: str
    source: str
    score: float = 0.0


async def search_memory(
    index: MemoryIndex,
    query: str,
    source_filter: str | None = None,
    limit: int = 10,
) -> list[SearchResult]:
    """Hybrid search pipeline.

    Args:
        index: Opened MemoryIndex instance.
        query: Search query string.
        source_filter: Optional source type filter ("global", "workspace", "session").
        limit: Maximum number of results to return.

    Returns:
        List of SearchResult sorted by weighted score (descending).
    """
    # Step 1: FTS5 keyword search
    raw_results = await index.search_fts(query, limit=limit * 3)
    logger.debug("FTS5 raw results: %d for query '%s'", len(raw_results), query)

    # Step 2: Filter and convert
    results: list[SearchResult] = []
    for row in raw_results:
        # Filter empty/template chunks
        if _is_content_free(row.get("text", "")):
            continue

        # Apply source filter
        source = row.get("source", "workspace")
        if source_filter and source != source_filter:
            continue

        results.append(
            SearchResult(
                chunk_id=str(row.get("rowid", "")),
                path=row.get("path", ""),
                text=row.get("text", ""),
                source=source,
                score=float(row.get("score", 0.0)),
            )
        )

    # Step 3: Apply source weight and re-score
    for r in results:
        weight = _SOURCE_WEIGHTS.get(r.source, 0.5)
        # Invert FTS5 rank (lower BM25 rank = better) and apply weight
        # For LIKE fallback, score is 0.0, skip rank inversion
        if r.score > 0:
            # FTS5 rank: lower is better. Normalize: 1/(1+rank) * weight
            r.score = weight / (1.0 + r.score)
        else:
            r.score = weight * 0.5  # LIKE fallback: medium relevance

    # Step 4: Sort by score descending and truncate
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:limit]


def _is_content_free(text: str) -> bool:
    """Check if text is an empty template chunk.

    Returns True for chunks that contain only HTML comments or whitespace.
    """
    stripped = text.strip()
    if not stripped:
        return True
    # Check if every non-empty line starts with a template marker
    non_empty = [line for line in stripped.split("\n") if line.strip()]
    if not non_empty:
        return True
    # If all non-empty lines are HTML comments, it's content-free
    return all(
        any(line.strip().startswith(t) for t in _EMPTY_TEMPLATES)
        for line in non_empty
    )


# ─── Session search strategy ──────────────────────────────────────────────────
# [MEM-05] 2026-08-09: Session-start memory query strategy extracted from
# MemoryHook.on_session_start (hooks/phase2_hooks.py). The policy — "take the
# last user message, truncate to 200 chars, search with limit=5" — belongs
# with the memory package, not inline in the hook adapter.


def build_session_query(messages) -> str | None:
    """Build a memory search query from a session's message list.

    Strategy: take the **last** user message's content, truncate to 200
    chars. Returns ``None`` if there are no user messages.

    Args:
        messages: Iterable of message objects with ``role`` and ``content``
            attributes (duck-typed; works with ``core.messages.UserMessage``
            and friends).

    Returns:
        The query string (≤200 chars), or ``None`` if no user messages.
    """
    user_messages = [m for m in messages if m.role == "user"]
    if not user_messages:
        return None
    return user_messages[-1].content[:200]


async def search_for_session(
    index: MemoryIndex,
    messages,
    *,
    limit: int = 5,
) -> list[SearchResult]:
    """Search memory for the current session using the session-start policy.

    Convenience wrapper: builds the query via :func:`build_session_query`
    (last user message, truncated to 200 chars) and runs
    :func:`search_memory` with the given limit.

    Args:
        index: Opened MemoryIndex instance.
        messages: Iterable of message objects (see :func:`build_session_query`).
        limit: Maximum number of results to return (default 5).

    Returns:
        List of SearchResult sorted by weighted score (descending).
        Empty list if no user messages or no matches.
    """
    query = build_session_query(messages)
    if query is None:
        return []
    return await search_memory(index, query, limit=limit)
