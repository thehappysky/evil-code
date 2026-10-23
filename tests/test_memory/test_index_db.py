"""Tests for memory/index_db.py — MemoryIndex with SQLite + FTS5."""

from pathlib import Path

import pytest

from code_agent.memory.index_db import MemoryIndex, _sanitize_fts5_query


@pytest.fixture
def index_db_path(tmp_path):
    return tmp_path / "test_index.db"


@pytest.fixture
async def memory_index(index_db_path):
    idx = MemoryIndex(index_db_path)
    await idx.open()
    yield idx
    await idx.close()


class TestSanitizeFts5Query:
    def test_basic(self):
        result = _sanitize_fts5_query("hello world")
        # Each term is quoted for FTS5 matching
        assert '"hello"' in result
        assert '"world"' in result

    def test_special_chars_removed(self):
        result = _sanitize_fts5_query('test*query"with:chars')
        assert "*" not in result
        assert ":" not in result
        # FTS5 quotes are part of the expected output (safe quoting)
        assert '"test"' in result

    def test_empty_query(self):
        assert _sanitize_fts5_query("") == '""'


class TestMemoryIndex:
    @pytest.mark.asyncio
    async def test_open_and_close(self, index_db_path):
        idx = MemoryIndex(index_db_path)
        await idx.open()
        assert idx._db is not None
        await idx.close()
        assert idx._db is None

    @pytest.mark.asyncio
    async def test_reindex_and_search(self, memory_index, tmp_path):
        """Index a MEMORY.md and search it."""
        md_path = tmp_path / "MEMORY.md"
        md_path.write_text("""# Memory

## Session Management
Sessions are stored as JSONL files.
Use SessionStore for reading and writing.

## Authentication
OAuth2 tokens are refreshed automatically.
""", encoding="utf-8")

        count = await memory_index.reindex_file(md_path, source="workspace")
        assert count >= 1

        total = await memory_index.get_chunk_count()
        assert total >= 1

        # Search
        results = await memory_index.search_fts("session JSONL", limit=5)
        assert len(results) >= 1
        # Should match the session section
        found = any("JSONL" in str(r.get("text", "")) for r in results)
        assert found, f"No result containing 'JSONL' in: {results}"

    @pytest.mark.asyncio
    async def test_reindex_unchanged_skips(self, memory_index, tmp_path):
        """Re-indexing an unchanged file should be a no-op."""
        md_path = tmp_path / "MEMORY.md"
        md_path.write_text("## Test\nContent here.", encoding="utf-8")

        count1 = await memory_index.reindex_file(md_path)
        assert count1 > 0

        count2 = await memory_index.reindex_file(md_path)
        assert count2 == 0  # No changes

    @pytest.mark.asyncio
    async def test_reindex_changed_updates(self, memory_index, tmp_path):
        """Re-indexing a changed file should update chunks."""
        md_path = tmp_path / "MEMORY.md"
        md_path.write_text("## Test\nVersion 1.", encoding="utf-8")

        count1 = await memory_index.reindex_file(md_path)
        assert count1 > 0

        md_path.write_text("## Test\nVersion 2. Changed.\n\n## New\nNew section.", encoding="utf-8")
        count2 = await memory_index.reindex_file(md_path)
        assert count2 > 0  # Should update changed + add new

    @pytest.mark.asyncio
    async def test_delete_chunks(self, memory_index, tmp_path):
        md_path = tmp_path / "MEMORY.md"
        md_path.write_text("## A\nContent A.\n\n## B\nContent B.", encoding="utf-8")

        await memory_index.reindex_file(md_path)
        total_before = await memory_index.get_chunk_count()
        assert total_before >= 2

        deleted = await memory_index.delete_chunks_for_file(md_path)
        assert deleted == total_before
        assert await memory_index.get_chunk_count() == 0

    @pytest.mark.asyncio
    async def test_search_like_fallback(self, memory_index, tmp_path):
        """LIKE fallback search should work (even when FTS5 is available)."""
        md_path = tmp_path / "MEMORY.md"
        md_path.write_text("## Alpha\nContent about alpha.", encoding="utf-8")

        await memory_index.reindex_file(md_path)

        # Force LIKE search by temporarily disabling FTS5
        fts5_flag = memory_index._fts5_available
        memory_index._fts5_available = False
        try:
            results = await memory_index.search_fts("alpha")
            assert len(results) >= 1
        finally:
            memory_index._fts5_available = fts5_flag

    @pytest.mark.asyncio
    async def test_empty_file(self, memory_index, tmp_path):
        md_path = tmp_path / "empty.md"
        md_path.write_text("", encoding="utf-8")
        count = await memory_index.reindex_file(md_path)
        assert count == 0

    @pytest.mark.asyncio
    async def test_nonexistent_file(self, memory_index, tmp_path):
        count = await memory_index.reindex_file(tmp_path / "nonexistent.md")
        assert count == 0
