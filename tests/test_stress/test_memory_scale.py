"""Stress: cross-session memory write path under repeated / many sessions."""

import pytest

from code_agent.core.session import Session
from code_agent.hooks.memory_write_hook import MemoryWriteHook
from code_agent.memory.file_store import MemoryStore
from code_agent.memory.index_db import MemoryIndex
from code_agent.memory.search import search_memory


@pytest.fixture
async def env(tmp_path):
    index = MemoryIndex(tmp_path / "memory" / "index.db")
    await index.open()
    store = MemoryStore(root_dir=tmp_path / "memory")
    yield index, store, str(tmp_path / "project")
    await index.close()


class TestMemoryWriteScale:
    @pytest.mark.asyncio
    async def test_same_session_many_runs_stays_searchable(self, env):
        """10 runs of one session: log accumulates, index stays searchable."""
        index, store, cwd = env
        hook = MemoryWriteHook(index, store, cwd)
        session = Session(system_prompt="Sys")
        session._session_id = "s_many_runs_0001"

        await hook.on_session_start(session)
        for i in range(10):
            session.add_user_message(f"note {i} about keyword{i}")
            await hook.on_session_end(session)

        logs = list((store.workspace_dir(cwd) / "sessions").glob("*.md"))
        assert len(logs) == 1  # same session → same log file

        results = await search_memory(index, "keyword7", limit=5)
        assert results
        assert any("keyword7" in r.text for r in results)

    @pytest.mark.asyncio
    async def test_many_sessions_index_stays_bounded(self, env):
        """50 sessions: each log exists, search hits, chunk count stays sane."""
        index, store, cwd = env

        for i in range(50):
            session = Session(system_prompt="Sys")
            session._session_id = f"session-{i:03d}"
            session.add_user_message(f"project {i} uses feature-{i}")
            hook = MemoryWriteHook(index, store, cwd)
            await hook.on_session_start(session)
            await hook.on_session_end(session)

        logs = list((store.workspace_dir(cwd) / "sessions").glob("*.md"))
        assert len(logs) == 50

        results = await search_memory(index, "feature-33", limit=5)
        assert results
        assert len(results) <= 5

        # Every session log was indexed exactly once (50 files × 1 chunk).
        assert await index.get_chunk_count() == 50

        # And the specific content really lives in the index (direct check,
        # since the FTS5 query is OR-based and returns top-ranked matches).
        cursor = await index._db.execute(
            "SELECT COUNT(*) AS cnt FROM chunks WHERE text LIKE ?",
            ("%feature-33%",),
        )
        row = await cursor.fetchone()
        assert row["cnt"] == 1
