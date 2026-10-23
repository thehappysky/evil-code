"""Tests for hooks/phase2_hooks.py — Phase 2 AgentHook implementations."""

from pathlib import Path

import pytest

from code_agent.core.hooks import HookDecision
from code_agent.core.session import Session
from code_agent.hooks.phase2_hooks import (
    CompactionHook,
    EditTrackerHook,
    MemoryHook,
    PersistenceHook,
)
from code_agent.tools.result import ToolResult


class TestEditTrackerHook:
    @pytest.fixture
    def hook(self):
        return EditTrackerHook()

    @pytest.mark.asyncio
    async def test_tracks_file_edit(self, hook):
        await hook.on_tool_result(
            "file_edit", {"file_path": "/src/main.py"},
            ToolResult.ok("edited"),
        )
        files = hook.get_edited_files()
        assert len(files) == 1
        assert files[0].endswith("src/main.py") or files[0].endswith("src\\main.py")

    @pytest.mark.asyncio
    async def test_tracks_file_write(self, hook):
        await hook.on_tool_result(
            "file_write", {"file_path": "/src/new.py"},
            ToolResult.ok("created"),
        )
        files = hook.get_edited_files()
        assert len(files) == 1
        assert files[0].endswith("src/new.py") or files[0].endswith("src\\new.py")

    @pytest.mark.asyncio
    async def test_ignores_error_result(self, hook):
        await hook.on_tool_result(
            "file_write", {"file_path": "/src/fail.py"},
            ToolResult.error("permission denied"),
        )
        assert hook.get_edited_files() == []

    @pytest.mark.asyncio
    async def test_ignores_non_edit_tools(self, hook):
        await hook.on_tool_result(
            "shell_exec", {"command": "echo hello"},
            ToolResult.ok("hello"),
        )
        assert hook.get_edited_files() == []

    @pytest.mark.asyncio
    async def test_multiple_edits_deduplicated(self, hook):
        await hook.on_tool_result(
            "file_edit", {"file_path": "/src/main.py"},
            ToolResult.ok("edit1"),
        )
        await hook.on_tool_result(
            "file_edit", {"file_path": "/src/main.py"},
            ToolResult.ok("edit2"),
        )
        assert len(hook.get_edited_files()) == 1

    @pytest.mark.asyncio
    async def test_clear(self, hook):
        await hook.on_tool_result(
            "file_write", {"file_path": "/src/a.py"},
            ToolResult.ok("created"),
        )
        assert len(hook.get_edited_files()) == 1
        hook.clear()
        assert hook.get_edited_files() == []

    @pytest.mark.asyncio
    async def test_resolves_relative_path(self, hook, tmp_path):
        import os
        test_file = tmp_path / "test.py"
        await hook.on_tool_result(
            "file_write", {"file_path": str(test_file)},
            ToolResult.ok("created"),
        )
        files = hook.get_edited_files()
        assert len(files) == 1
        assert "test.py" in files[0]


class TestPersistenceHook:
    @pytest.fixture
    def hook(self, tmp_path):
        from code_agent.storage.session_store import SessionStore
        store = SessionStore(root_dir=tmp_path / "sessions")
        return PersistenceHook(store, cwd="/test/project")

    @pytest.mark.asyncio
    async def test_on_session_start_initializes(self, hook):
        session = Session(system_prompt="You are helpful.")
        await hook.on_session_start(session)
        assert hook._initialized
        assert hook._session_id is not None

    @pytest.mark.asyncio
    async def test_on_turn_end_persists(self, hook):
        session = Session(system_prompt="You are helpful.")
        session.add_user_message("hello")
        await hook.on_session_start(session)
        await hook.on_turn_end(session, turn=1)
        # Should not raise; persistence is best-effort

    async def test_compacted_history_rewrites_file(self, hook, tmp_path):
        """[2026-08-05] After compaction the JSONL is rewritten, not appended."""
        from code_agent.core.messages import SystemMessage, UserMessage
        from code_agent.storage.session_store import SessionStore

        session = Session(system_prompt="You are helpful.")
        session.add_user_message("hello")
        await hook.on_session_start(session)

        # Simulate compaction: replace the in-memory history.
        session.replace_history([
            SystemMessage(content="You are helpful."),
            UserMessage(
                content="<conversation-history-summary>compacted</conversation-history-summary>",
            ),
        ])
        assert session._history_replaced is True

        await hook.on_turn_end(session, turn=1)
        assert session._history_replaced is False

        store = SessionStore(root_dir=tmp_path / "sessions")
        _, msgs = await store.load_session(hook._session_id, "/test/project")
        assert [m["role"] for m in msgs] == ["system", "user"]
        assert "compacted" in msgs[-1]["content"]

    async def test_on_session_end_rewrites_compacted_history(self, hook, tmp_path):
        """[2026-08-05] max_turns/error termination still persists a compacted history."""
        from code_agent.core.messages import SystemMessage, UserMessage
        from code_agent.storage.session_store import SessionStore

        session = Session(system_prompt="You are helpful.")
        session.add_user_message("hello")
        await hook.on_session_start(session)
        session.replace_history([
            SystemMessage(content="You are helpful."),
            UserMessage(content="summary only"),
        ])

        await hook.on_session_end(session, reason="max_turns")
        assert session._history_replaced is False

        store = SessionStore(root_dir=tmp_path / "sessions")
        _, msgs = await store.load_session(hook._session_id, "/test/project")
        assert [m["role"] for m in msgs] == ["system", "user"]
        assert "summary only" in msgs[-1]["content"]

    async def test_save_full_history_failure_keeps_flag(self, hook, tmp_path, monkeypatch):
        """[L5] A failed rewrite must not clear the flag; the next attempt retries."""
        from code_agent.core.messages import SystemMessage, UserMessage
        from code_agent.storage.session_store import SessionStore

        session = Session(system_prompt="You are helpful.")
        session.add_user_message("hello")
        await hook.on_session_start(session)
        session.replace_history([
            SystemMessage(content="You are helpful."),
            UserMessage(content="compacted"),
        ])

        async def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(hook._store, "save_full_history", boom)
        await hook.on_turn_end(session, turn=1)  # must not raise
        assert session._history_replaced is True  # retry needed

        monkeypatch.undo()
        await hook.on_turn_end(session, turn=1)
        assert session._history_replaced is False

        store = SessionStore(root_dir=tmp_path / "sessions")
        _, msgs = await store.load_session(hook._session_id, "/test/project")
        assert [m["role"] for m in msgs] == ["system", "user"]
        assert "compacted" in msgs[-1]["content"]

    @pytest.mark.asyncio
    async def test_skip_when_no_session_id(self, hook):
        session = Session()
        hook._session_id = None
        # Should not raise
        await hook.on_turn_end(session, turn=1)


class TestMemoryHook:
    @pytest.fixture
    async def hook(self, tmp_path):
        from code_agent.memory.index_db import MemoryIndex
        idx = MemoryIndex(tmp_path / "test.db")
        await idx.open()
        hook = MemoryHook(idx)
        yield hook
        await idx.close()

    @pytest.mark.asyncio
    async def test_on_session_start_no_messages(self, hook):
        session = Session(system_prompt="Agent.")
        # No user messages yet — should not raise
        await hook.on_session_start(session)

    @pytest.mark.asyncio
    async def test_only_injects_once(self, hook):
        session = Session(system_prompt="Agent.")
        session.add_user_message("hello world")
        await hook.on_session_start(session)
        msg_count_before = len(session.messages)
        await hook.on_session_start(session)
        assert len(session.messages) == msg_count_before


class TestCompactionHook:
    @pytest.fixture
    def hook(self):
        from code_agent.compaction.policy import CompactionPolicy
        # Create a policy that never triggers (min_tokens very high)
        policy = CompactionPolicy(min_tokens=1_000_000_000)
        from unittest.mock import MagicMock
        client = MagicMock()
        return CompactionHook(policy, client)

    @pytest.mark.asyncio
    async def test_no_trigger_when_below_threshold(self, hook):
        session = Session(system_prompt="Agent.")
        session.add_user_message("hello")
        # Token count is tiny — should not trigger
        await hook.on_turn_start(session, turn=1, tools=[])
        assert hook.compaction_count == 0

    @pytest.mark.asyncio
    async def test_handles_exception_gracefully(self, hook):
        """Hook should catch exceptions and continue."""
        session = Session(system_prompt="Agent.")
        # Force an error by passing None session
        try:
            await hook.on_turn_start(None, turn=1, tools=[])
        except Exception:
            pass  # Hook should handle internally

    @pytest.mark.asyncio
    async def test_reduction_guard_skips_when_barely_helpful(self, monkeypatch):
        """[Phase 6] Compaction is skipped when the reduction is < 20%."""
        from code_agent.compaction.policy import CompactionPolicy
        from unittest.mock import MagicMock

        # Policy that triggers immediately (tiny window / threshold).
        policy = CompactionPolicy(
            context_window=1000, threshold_percent=10, min_tokens=1,
        )
        client = MagicMock()
        hook = CompactionHook(policy, client)

        # A huge "summary" makes the assembled history ~ the original size,
        # so the reduction guard must refuse to replace the history.
        async def _huge_summary(*a, **k):
            return "x" * 5000

        monkeypatch.setattr(
            "code_agent.hooks.phase2_hooks.generate_summary", _huge_summary,
        )

        session = Session(system_prompt="Agent.")
        session.add_user_message("q" * 400)   # ~100 tokens → triggers policy
        session.add_assistant_message(text="a" * 400)

        await hook.on_turn_start(session, turn=0, tools=[])
        # Guard fired: reduction < 20% → no replace, no compaction counted.
        assert hook.compaction_count == 0

    @pytest.mark.asyncio
    async def test_reduction_guard_allows_real_compaction(self, monkeypatch):
        """[Phase 6] A genuinely small summary passes the guard."""
        from code_agent.compaction.policy import CompactionPolicy
        from unittest.mock import MagicMock

        policy = CompactionPolicy(
            context_window=1000, threshold_percent=10, min_tokens=1,
        )
        client = MagicMock()
        hook = CompactionHook(policy, client)

        async def _small_summary(*a, **k):
            return "short summary"

        monkeypatch.setattr(
            "code_agent.hooks.phase2_hooks.generate_summary", _small_summary,
        )

        session = Session(system_prompt="Agent.")
        # Large messages (well over the 500-token preserve budget) so the
        # short summary really replaces most of the conversation.
        session.add_user_message("q" * 4000)
        session.add_assistant_message(text="a" * 4000)

        await hook.on_turn_start(session, turn=0, tools=[])
        # Small summary → real reduction → compaction happened.
        assert hook.compaction_count == 1

    @pytest.mark.asyncio
    async def test_edit_tracker_files_appear_in_reminder(self, monkeypatch, tmp_path):
        """[2026-08-05] EditTrackerHook output reaches the compaction reminder."""
        from code_agent.compaction.policy import CompactionPolicy
        from unittest.mock import MagicMock

        policy = CompactionPolicy(
            context_window=1000, threshold_percent=10, min_tokens=1,
        )
        client = MagicMock()

        tracker = EditTrackerHook()
        target = tmp_path / "src" / "a.py"
        target.parent.mkdir(parents=True)
        target.write_text("x", encoding="utf-8")
        await tracker.on_tool_result(
            "file_write", {"file_path": str(target)}, ToolResult.ok("created"),
        )

        hook = CompactionHook(policy, client, edit_tracker=tracker)

        async def _small_summary(*a, **k):
            return "short summary"

        monkeypatch.setattr(
            "code_agent.hooks.phase2_hooks.generate_summary", _small_summary,
        )

        session = Session(system_prompt="Agent.")
        session.add_user_message("q" * 4000)
        session.add_assistant_message(text="a" * 4000)

        await hook.on_turn_start(session, turn=0, tools=[])
        assert hook.compaction_count == 1
        texts = [m.content for m in session.messages if m.role == "user"]
        assert any("Edited files" in t for t in texts)
        assert any("a.py" in t for t in texts)

    @pytest.mark.asyncio
    async def test_edited_files_deduped_and_accumulated(self, monkeypatch, tmp_path):
        """[D-L6] Same file edited twice → one entry; multiple files accumulate."""
        from code_agent.compaction.policy import CompactionPolicy
        from unittest.mock import MagicMock

        policy = CompactionPolicy(
            context_window=1000, threshold_percent=10, min_tokens=1,
        )
        client = MagicMock()

        tracker = EditTrackerHook()
        a = tmp_path / "src" / "a.py"
        b = tmp_path / "src" / "b.py"
        a.parent.mkdir(parents=True)
        a.write_text("x", encoding="utf-8")
        b.write_text("y", encoding="utf-8")
        for path in (a, a, b):  # a twice → dedup
            await tracker.on_tool_result(
                "file_write", {"file_path": str(path)},
                ToolResult.ok("created"),
            )
        assert len(tracker.get_edited_files()) == 2

        hook = CompactionHook(policy, client, edit_tracker=tracker)

        async def _small_summary(*a_, **k):
            return "short summary"

        monkeypatch.setattr(
            "code_agent.hooks.phase2_hooks.generate_summary", _small_summary,
        )

        session = Session(system_prompt="Agent.")
        session.add_user_message("q" * 4000)
        session.add_assistant_message(text="a" * 4000)
        await hook.on_turn_start(session, turn=0, tools=[])

        texts = [m.content for m in session.messages if m.role == "user"]
        reminder = next(t for t in texts if "Edited files" in t)
        assert reminder.count("- ") == 2  # deduped: a.py and b.py, once each
        assert "a.py" in reminder and "b.py" in reminder
