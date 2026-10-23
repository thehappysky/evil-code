"""Scale tests: correctness at volume.

These exercises push individual code paths to larger inputs than the unit
tests in tests/test_compaction, tests/test_api, etc. The goal is correctness
at scale (no crashes, complete results), not strict wall-clock timing:
assertions are functional, and any time guard is deliberately generous so
CI never hard-fails on normal timing variance.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from code_agent.api.sse import iter_sse_events
from code_agent.compaction.assembler import count_preserved_turns
from code_agent.helpers.token_counter import estimate_tokens
from code_agent.memory.index_db import MemoryIndex


def _make_response(content: str) -> httpx.Response:
    """Create a mock httpx.Response from raw SSE text content."""
    return httpx.Response(
        status_code=200,
        content=content.encode("utf-8"),
        headers={"content-type": "text/event-stream"},
    )


class TestCountPreservedTurnsScale:
    """count_preserved_turns over a large conversation."""

    def test_large_conversation(self) -> None:
        """3000+ messages: valid split, complete coverage, order preserved.

        ``count_preserved_turns`` walks backwards and builds the preserved
        list with ``list.insert(0, ...)`` (O(n^2) on the preserved window).
        This asserts correctness at scale and is bounded by a generous time
        guard so an accidental O(n^2) blow-up is visible without making CI
        flaky on timing noise.
        """
        message_count = 3000
        all_messages = [
            {
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"conversation message {i} with some body text",
            }
            for i in range(message_count)
        ]

        started = time.monotonic()
        to_summarize, preserved = count_preserved_turns(
            all_messages, max_recent_tokens=10_000_000
        )
        elapsed = time.monotonic() - started

        # Correctness at scale.
        assert len(to_summarize) + len(preserved) == message_count
        # Generous budget -> everything is preserved (walk never breaks early).
        assert len(preserved) == message_count
        assert to_summarize == []
        # insert(0) must not scramble the original order.
        assert preserved[0]["content"] == all_messages[0]["content"]
        assert preserved[-1]["content"] == all_messages[-1]["content"]
        assert [m["role"] for m in preserved] == [m["role"] for m in all_messages]

        # Soft guard: bound the O(n^2) insert(0) cost; far above normal runtime.
        assert elapsed < 30.0, f"count_preserved_turns too slow at scale: {elapsed:.1f}s"


class TestSSEParserScale:
    """iter_sse_events under heavy streams."""

    async def test_thousands_of_events(self) -> None:
        """A stream of thousands of events is fully parsed, in order."""
        event_count = 5000
        content = "".join(
            f"data: {json.dumps({'i': i})}\n\n" for i in range(event_count)
        )

        parsed = [e async for e in iter_sse_events(_make_response(content))]

        assert len(parsed) == event_count
        assert parsed[0] == {"i": 0}
        assert parsed[event_count - 1] == {"i": event_count - 1}

    async def test_multimegabyte_payload(self) -> None:
        """A single multi-MB data payload survives round-trip parsing."""
        payload_size = 2 * 1024 * 1024  # 2 MiB of text inside the JSON
        payload = json.dumps({"text": "x" * payload_size})
        content = f"data: {payload}\n\n"

        parsed = [e async for e in iter_sse_events(_make_response(content))]

        assert len(parsed) == 1
        assert len(parsed[0]["text"]) == payload_size
        assert parsed[0]["text"] == "x" * payload_size

    async def test_no_trailing_blank_line(self) -> None:
        """A stream that ends without the blank-line delimiter must flush.

        The parser only yields on a blank line, so the final event relies on
        the end-of-stream flush branch to be emitted at all.
        """
        content = 'data: {"a": 1}\n\ndata: {"b": 2}\n'

        parsed = [e async for e in iter_sse_events(_make_response(content))]

        assert parsed == [{"a": 1}, {"b": 2}]

    async def test_many_events_no_trailing_blank_line(self) -> None:
        """Thousands of events, last one flushed without a trailing delimiter."""
        event_count = 2000
        lines = "".join(
            f"data: {json.dumps({'i': i})}\n\n" for i in range(event_count - 1)
        )
        # Final event has NO following blank line.
        content = lines + f"data: {json.dumps({'i': event_count - 1})}\n"

        parsed = [e async for e in iter_sse_events(_make_response(content))]

        assert len(parsed) == event_count
        assert parsed[-1] == {"i": event_count - 1}


class TestTokenCounterScale:
    """estimate_tokens on very large strings."""

    def test_large_string(self) -> None:
        """A 10MB string yields a positive estimate without crashing."""
        text = "x" * (10 * 1024 * 1024)

        started = time.monotonic()
        estimate = estimate_tokens(text)
        elapsed = time.monotonic() - started

        assert estimate > 0
        assert estimate == (10 * 1024 * 1024) // 4  # bytes // 4 heuristic
        assert elapsed < 10.0


class TestMemoryIndexScale:
    """MemoryIndex.reindex_file with a large multi-chunk document."""

    async def test_reindex_large_document(self, tmp_path: Path) -> None:
        """Indexing a many-chunk MEMORY.md completes and search finds a needle."""
        section_count = 1000
        sections = [
            f"## Section {i}\n\n"
            f"Paragraph body {i} with enough sentence text to form a real chunk, "
            f"carrying the unique marker zebra{i} for search."
            for i in range(section_count)
        ]
        md_path = tmp_path / "MEMORY.md"
        md_path.write_text("\n\n".join(sections), encoding="utf-8")

        index = MemoryIndex(tmp_path / "index.db")
        await index.open()
        try:
            started = time.monotonic()
            count = await index.reindex_file(md_path, source="workspace")
            elapsed = time.monotonic() - started

            assert count >= section_count
            assert await index.get_chunk_count() >= section_count

            # A needle unique to one middle chunk must still be found.
            results = await index.search_fts("zebra500", limit=10)
            assert results, "search returned nothing after large reindex"
            assert any("zebra500" in str(r.get("text", "")) for r in results)

            assert elapsed < 60.0, f"large reindex too slow: {elapsed:.1f}s"
        finally:
            await index.close()
