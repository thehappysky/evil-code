"""Tests for api/sse.py: SSE stream parser.

Covers:
- data: line parsing
- event: type marking
- [DONE] terminator
- multi-line data joining
- comment lines (keepalive)
- invalid JSON skipping
"""

from __future__ import annotations

import json

import httpx
import pytest

from code_agent.api.sse import iter_sse_events


def _make_response(content: str) -> httpx.Response:
    """Create a mock httpx.Response from SSE text content."""
    return httpx.Response(
        status_code=200,
        content=content.encode("utf-8"),
        headers={"content-type": "text/event-stream"},
    )


class TestSSEParsing:
    async def test_simple_data_line(self):
        """Single data: line yields one JSON event."""
        sse = 'data: {"text": "hello"}\n\n'
        response = _make_response(sse)
        events = [e async for e in iter_sse_events(response)]
        assert len(events) == 1
        assert events[0]["text"] == "hello"

    async def test_done_terminator(self):
        """data: [DONE] yields {"_done": True}."""
        sse = 'data: {"text": "hi"}\n\ndata: [DONE]\n\n'
        response = _make_response(sse)
        events = [e async for e in iter_sse_events(response)]
        assert len(events) == 2
        assert events[0]["text"] == "hi"
        assert events[1] == {"_done": True}

    async def test_event_type_marking(self):
        """event: line attaches _event key to the following data."""
        sse = 'event: content_block_delta\ndata: {"type": "text_delta", "text": "x"}\n\n'
        response = _make_response(sse)
        events = [e async for e in iter_sse_events(response)]
        assert len(events) == 1
        assert events[0]["_event"] == "content_block_delta"
        assert events[0]["text"] == "x"

    async def test_multi_line_data(self):
        """Multiple data: lines are joined with newline."""
        sse = 'data: {"part1":\ndata: "value"}\n\n'
        response = _make_response(sse)
        events = [e async for e in iter_sse_events(response)]
        assert len(events) == 1
        assert events[0]["part1"] == "value"

    async def test_comment_lines_skipped(self):
        """Lines starting with : are comments (keepalive), skipped."""
        sse = ': keepalive\n\ndata: {"text": "hello"}\n\n'
        response = _make_response(sse)
        events = [e async for e in iter_sse_events(response)]
        assert len(events) == 1
        assert events[0]["text"] == "hello"

    async def test_invalid_json_skipped(self):
        """Invalid JSON in data: line is skipped without error."""
        sse = 'data: not-json\n\ndata: {"text": "valid"}\n\n'
        response = _make_response(sse)
        events = [e async for e in iter_sse_events(response)]
        assert len(events) == 1
        assert events[0]["text"] == "valid"
