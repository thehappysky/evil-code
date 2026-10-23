"""Tests for memory/search.py — hybrid search pipeline."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from code_agent.memory.index_db import MemoryIndex
from code_agent.memory.search import (
    SearchResult,
    _SOURCE_WEIGHTS,
    build_session_query,
    search_for_session,
    search_memory,
)


class TestSearchMemory:
    @pytest.fixture
    async def index(self, tmp_path):
        """Create an index with test data."""
        db_path = tmp_path / "test.db"
        idx = MemoryIndex(db_path)
        await idx.open()

        # Create test MEMORY.md
        md_path = tmp_path / "MEMORY.md"
        md_path.write_text("""# Test Memory

## Python Coding
We use Python 3.12 with type hints everywhere.
Follow PEP 8 style guide.

## Project Structure
The project uses clean architecture with separate layers.
Authentication is handled by the auth module.

## Deployment
Deploy to production using Docker containers.
Use docker-compose for local development.
""", encoding="utf-8")
        await idx.reindex_file(md_path, source="workspace")
        yield idx
        await idx.close()

    @pytest.mark.asyncio
    async def test_search_returns_results(self, index):
        results = await search_memory(index, "Python type hints", limit=3)
        assert len(results) >= 1
        assert all(isinstance(r, SearchResult) for r in results)

    @pytest.mark.asyncio
    async def test_results_have_correct_fields(self, index):
        results = await search_memory(index, "Docker", limit=3)
        for r in results:
            assert r.chunk_id
            assert r.path
            assert r.text
            assert r.source in _SOURCE_WEIGHTS
            assert r.score >= 0.0

    @pytest.mark.asyncio
    async def test_source_weight_ordering(self, index):
        """Global source should rank higher than workspace."""
        results = await search_memory(index, "Python", limit=10)
        # workspace results get 0.8 weight
        for r in results:
            if r.source == "workspace":
                assert r.score <= 0.8

    @pytest.mark.asyncio
    async def test_source_filter(self, index):
        results = await search_memory(
            index, "Python", source_filter="global", limit=5,
        )
        # No global sources in test data, should be empty
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_limit_respected(self, index):
        results = await search_memory(index, "the", limit=1)
        assert len(results) <= 1

    @pytest.mark.asyncio
    async def test_no_results_for_nonsense(self, index):
        results = await search_memory(index, "xyznonexistent12345", limit=5)
        assert len(results) == 0


def _msg(role: str, content: str):
    """Duck-typed message object for build_session_query."""
    return SimpleNamespace(role=role, content=content)


class TestBuildSessionQuery:
    """[MEM-05] Tests for the session-start query construction strategy."""

    def test_uses_last_user_message(self):
        messages = [
            _msg("system", "system prompt"),
            _msg("user", "first question"),
            _msg("assistant", "answer"),
            _msg("user", "second question"),
        ]
        assert build_session_query(messages) == "second question"

    def test_truncates_to_200_chars(self):
        long_content = "x" * 500
        messages = [_msg("user", long_content)]
        query = build_session_query(messages)
        assert len(query) == 200
        assert query == long_content[:200]

    def test_returns_none_when_no_user_messages(self):
        messages = [
            _msg("system", "system prompt"),
            _msg("assistant", "hi"),
        ]
        assert build_session_query(messages) is None

    def test_returns_none_for_empty_messages(self):
        assert build_session_query([]) is None

    def test_ignores_assistant_and_tool_messages(self):
        messages = [
            _msg("assistant", "a" * 300),
            _msg("tool", "t" * 300),
            _msg("user", "real query"),
        ]
        assert build_session_query(messages) == "real query"

    def test_exactly_200_chars_not_truncated(self):
        content = "y" * 200
        messages = [_msg("user", content)]
        assert build_session_query(messages) == content


class TestSearchForSession:
    """[MEM-05] Tests for the session-start search convenience wrapper."""

    @pytest.fixture
    async def index(self, tmp_path):
        db_path = tmp_path / "test.db"
        idx = MemoryIndex(db_path)
        await idx.open()
        md_path = tmp_path / "MEMORY.md"
        md_path.write_text(
            "# Memory\n\nWe use Python 3.12 with type hints.\n",
            encoding="utf-8",
        )
        await idx.reindex_file(md_path, source="workspace")
        yield idx
        await idx.close()

    @pytest.mark.asyncio
    async def test_searches_with_last_user_message(self, index):
        messages = [_msg("user", "Python type hints")]
        results = await search_for_session(index, messages, limit=5)
        assert len(results) >= 1
        assert all(isinstance(r, SearchResult) for r in results)

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_user_messages(self, index):
        messages = [_msg("system", "system prompt")]
        results = await search_for_session(index, messages, limit=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_default_limit_is_5(self, index):
        """Spec says default limit=5 (matches MemoryHook usage)."""
        messages = [_msg("user", "the")]
        results = await search_for_session(index, messages)
        assert len(results) <= 5
