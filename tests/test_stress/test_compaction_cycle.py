"""Stress: repeated compaction + persistence cycles must stay consistent."""

import pytest

from code_agent.compaction.policy import CompactionPolicy
from code_agent.core.session import Session
from code_agent.hooks.phase2_hooks import CompactionHook, PersistenceHook
from code_agent.storage.session_store import SessionStore


async def _load(store: SessionStore, sid: str, cwd: str) -> list[dict]:
    _, msgs = await store.load_session(sid, cwd)
    return msgs


class TestCompactionCycle:
    @pytest.mark.asyncio
    async def test_ten_compaction_cycles_no_history_bloat(self, tmp_path, monkeypatch):
        """10 rounds of append→compact→rewrite: file always equals the live
        session, old content never survives, size stays bounded."""
        from unittest.mock import MagicMock

        async def _small_summary(*a, **k):
            return "short summary"

        monkeypatch.setattr(
            "code_agent.hooks.phase2_hooks.generate_summary", _small_summary,
        )

        policy = CompactionPolicy(
            context_window=1000, threshold_percent=10, min_tokens=1,
        )
        client = MagicMock()
        store = SessionStore(root_dir=tmp_path / "sessions")
        cwd = str(tmp_path)
        compaction = CompactionHook(policy, client)
        persistence = PersistenceHook(store, cwd)

        session = Session(system_prompt="S")
        await persistence.on_session_start(session)
        sid = session._session_id
        session_dir = store._session_dir(cwd, sid)
        history_path = store._root / session_dir / "chat_history.jsonl"

        assert compaction.compaction_count == 0
        for i in range(10):
            # Append phase: small exchange, incremental append.
            session.add_user_message(f"R{i}")
            session.add_assistant_message(text=f"answer-{i}")
            await persistence.on_turn_end(session, turn=i)
            assert len(await _load(store, sid, cwd)) == len(
                session.to_api_messages(),
            )

            # Compaction phase: big exchange forces a rewrite.
            session.add_user_message("x" * 4000 + f"BIG{i}")
            session.add_assistant_message(text="y" * 4000)
            await compaction.on_turn_start(session, turn=i, tools=[])
            await persistence.on_turn_end(session, turn=i)

            assert session._history_replaced is False
            msgs = await _load(store, sid, cwd)
            assert len(msgs) == len(session.to_api_messages())
            text = history_path.read_text(encoding="utf-8")
            assert f"R{i}" not in text  # old content was summarized away
            assert f"BIG{i}" not in text
            assert history_path.stat().st_size < 30_000  # bounded

        assert compaction.compaction_count == 10

    @pytest.mark.asyncio
    async def test_stale_tmp_file_does_not_break_rewrite(self, tmp_path):
        """A leftover .tmp from a crashed rewrite must not corrupt reads, and
        the next save replaces it."""
        store = SessionStore(root_dir=tmp_path / "sessions")
        cwd = str(tmp_path)
        sid = "s_stale_tmp_0001"
        from code_agent.storage.models import SessionMeta

        await store.init_session(
            SessionMeta(session_id=sid, cwd=cwd, model_id="test"),
        )
        await store.append_message(sid, cwd, {"role": "user", "content": "hello"})

        history = store._root / store._session_dir(cwd, sid) / "chat_history.jsonl"
        stale = history.with_suffix(".tmp")
        stale.write_text("garbage from a crashed write", encoding="utf-8")

        _, msgs = await store.load_session(sid, cwd)
        assert [m["content"] for m in msgs] == ["hello"]  # tmp ignored

        await store.save_full_history(sid, cwd, [
            {"role": "system", "content": "S"},
            {"role": "user", "content": "compacted"},
        ])
        assert not stale.exists()  # os.replace removed it
        _, msgs = await store.load_session(sid, cwd)
        assert [m["content"] for m in msgs] == ["S", "compacted"]
