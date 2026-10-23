"""Tests for hooks/memory_write_hook.py — the cross-session memory WRITE path."""

import pytest

from code_agent.core.session import Session
from code_agent.hooks.memory_write_hook import (
    MemoryWriteHook,
    _session_to_markdown,
)
from code_agent.memory.file_store import MemoryStore
from code_agent.memory.index_db import MemoryIndex
from code_agent.memory.search import search_memory


class TestSessionToMarkdown:
    def test_excludes_system_and_keeps_roles(self):
        session = Session(system_prompt="You are a helper.")
        session.add_user_message("Fix the tests")
        session.add_assistant_message(text="On it.")

        md = _session_to_markdown(session.messages)
        assert "You are a helper." not in md
        assert "## user" in md
        assert "Fix the tests" in md
        assert "## assistant" in md
        assert "On it." in md

    def test_tool_results_are_marked(self):
        from code_agent.core.messages import ToolCallItem

        session = Session(system_prompt="S")
        tc = ToolCallItem(id="c1", name="file_read", arguments='{"file_path":"a.py"}')
        session.add_assistant_message(tool_calls=[tc])
        session.add_tool_result("c1", "file contents")

        md = _session_to_markdown(session.messages)
        assert "tool_result" in md
        assert "file_read" in md
        assert "file contents" in md


class TestMemoryWriteHook:
    @pytest.fixture
    async def env(self, tmp_path):
        index = MemoryIndex(tmp_path / "memory" / "index.db")
        await index.open()
        store = MemoryStore(root_dir=tmp_path / "memory")
        cwd = str(tmp_path / "project")
        hook = MemoryWriteHook(index, store, cwd)
        yield index, store, hook, cwd
        await index.close()

    @pytest.mark.asyncio
    async def test_captures_last_user_message_once(self, env):
        _, _, hook, _ = env
        session = Session(system_prompt="Sys")
        session.add_user_message("skills list (synthetic)")
        session.add_user_message("real prompt")
        await hook.on_session_start(session)
        assert hook._first_user == "real prompt"

        # Later runs of the same REPL session must not change the slug source.
        session2 = Session(system_prompt="Sys")
        session2.add_user_message("another prompt")
        await hook.on_session_start(session2)
        assert hook._first_user == "real prompt"

    @pytest.mark.asyncio
    async def test_writes_session_log_and_indexes_it(self, env):
        index, store, hook, cwd = env
        session = Session(system_prompt="Sys")
        session._session_id = "s_20260805_test1234"
        session.add_user_message("Fix the failing tests")
        session.add_assistant_message(text="I will investigate.")
        await hook.on_session_start(session)
        await hook.on_session_end(session)

        # A session log file was appended.
        logs = list((store.workspace_dir(cwd) / "sessions").glob("*.md"))
        assert len(logs) == 1
        text = logs[0].read_text(encoding="utf-8")
        assert "Fix the failing tests" in text

        # The log was indexed under source="session" and is searchable.
        results = await search_memory(index, "failing tests", limit=5)
        assert results
        assert any("failing tests" in r.text for r in results)

    @pytest.mark.asyncio
    async def test_reindexes_existing_memory_files(self, env):
        index, store, hook, cwd = env
        await store.write_workspace(cwd, "## Project\nUse pytest.\n")
        await store.write_global("## Global\nPrefer dark mode.\n")

        session = Session(system_prompt="Sys")
        session.add_user_message("hello")
        await hook.on_session_end(session)

        ws = await search_memory(index, "pytest", source_filter="workspace", limit=5)
        gl = await search_memory(index, "dark mode", source_filter="global", limit=5)
        assert any("pytest" in r.text for r in ws)
        assert any("dark mode" in r.text for r in gl)

    @pytest.mark.asyncio
    async def test_failure_degrades_gracefully(self, env):
        _, store, hook, _ = env
        session = Session(system_prompt="Sys")
        session.add_user_message("hello")
        hook._store = None  # break the write path
        await hook.on_session_end(session)  # must not raise

    @pytest.mark.asyncio
    async def test_empty_session_skips_write(self, env):
        _, store, hook, cwd = env
        session = Session(system_prompt="Sys")  # no user/assistant content
        await hook.on_session_end(session)
        logs = list((store.workspace_dir(cwd) / "sessions").glob("*.md"))
        assert logs == []

    @pytest.mark.asyncio
    async def test_index_not_opened_degrades(self, tmp_path):
        """[L5] MemoryIndex not opened → reindex raises → hook must not crash."""
        from code_agent.memory.file_store import MemoryStore
        from code_agent.memory.index_db import MemoryIndex

        # Constructed but NEVER opened — reindex_file raises RuntimeError.
        index = MemoryIndex(tmp_path / "never-opened.db")
        store = MemoryStore(root_dir=tmp_path / "memory")
        hook = MemoryWriteHook(index, store, str(tmp_path / "project"))

        session = Session(system_prompt="Sys")
        session._session_id = "s_never_opened_0001"
        session.add_user_message("hello memory")
        await hook.on_session_start(session)
        await hook.on_session_end(session)  # must not raise

    @pytest.mark.asyncio
    async def test_append_failure_degrades(self, env, monkeypatch):
        """[L5] Session-log append fails (disk/perm) → hook degrades gracefully."""
        _, store, hook, _ = env

        async def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(store, "append_session_log", boom)
        session = Session(system_prompt="Sys")
        session._session_id = "s_append_fail_0001"
        session.add_user_message("hello")
        await hook.on_session_start(session)
        await hook.on_session_end(session)  # must not raise
