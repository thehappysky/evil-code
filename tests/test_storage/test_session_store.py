"""Tests for storage/session_store.py — SessionStore + DeltaIterator."""

import asyncio
import json
from pathlib import Path

import pytest

from code_agent.storage.models import SessionMeta
from code_agent.storage.session_store import (
    DeltaIterator,
    SessionStore,
    _encode_cwd,
    _generate_session_id,
)


class TestEncodeCwd:
    def test_encode_basic(self):
        encoded = _encode_cwd("/home/user/project")
        assert len(encoded) > 0
        assert "=" not in encoded  # URL-safe, no padding

    def test_encode_decode_roundtrip(self):
        from base64 import urlsafe_b64decode
        cwd = "/test/path"
        encoded = _encode_cwd(cwd)
        decoded = urlsafe_b64decode(encoded + "==").decode("utf-8")
        assert decoded == cwd

    def test_encode_windows_path(self):
        encoded = _encode_cwd(r"C:\Users\test")
        assert ":" not in encoded  # No filesystem-incompatible chars


class TestGenerateSessionId:
    def test_format(self):
        sid = _generate_session_id()
        assert sid.startswith("s_")
        parts = sid.split("_")
        assert len(parts) == 3  # s_YYYYMMDD_hex8

    def test_uniqueness(self):
        ids = {_generate_session_id() for _ in range(100)}
        assert len(ids) == 100


class TestSessionStore:
    @pytest.fixture
    def store(self, tmp_path):
        return SessionStore(root_dir=tmp_path)

    @pytest.mark.asyncio
    async def test_init_session_creates_files(self, store):
        meta = SessionMeta(
            session_id="s_test_001", cwd="/project",
            model_id="test-model", title="Test",
        )
        await store.init_session(meta)

        session_dir = store._session_dir("/project", "s_test_001")
        assert (session_dir / "summary.json").exists()
        assert (session_dir / "chat_history.jsonl").exists()

    @pytest.mark.asyncio
    async def test_append_and_load(self, store):
        meta = SessionMeta(session_id="s_001", cwd="/proj", model_id="m")
        await store.init_session(meta)

        await store.append_message("s_001", "/proj", {"role": "user", "content": "hello"})
        await store.append_message("s_001", "/proj", {"role": "assistant", "content": "hi"})

        loaded_meta, messages = await store.load_session("s_001", "/proj")
        assert loaded_meta is not None
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_load_nonexistent_session(self, store):
        meta, messages = await store.load_session("nonexistent", "/proj")
        assert meta is None
        assert messages == []

    @pytest.mark.asyncio
    async def test_delta_reading(self, store):
        meta = SessionMeta(session_id="s_002", cwd="/proj", model_id="m")
        await store.init_session(meta)

        await store.append_message("s_002", "/proj", {"role": "user", "content": "msg1"})
        await store.append_message("s_002", "/proj", {"role": "user", "content": "msg2"})

        delta = await store.load_delta("s_002", "/proj", offset=0)
        assert len(delta) == 2

        # Read with offset after first message
        byte_offset = await store.get_history_byte_offset("s_002", "/proj")
        await store.append_message("s_002", "/proj", {"role": "user", "content": "msg3"})

        # Read only new messages
        delta2 = await store.load_delta("s_002", "/proj", offset=byte_offset)
        assert len(delta2) == 1
        assert delta2[0]["content"] == "msg3"

    @pytest.mark.asyncio
    async def test_update_meta(self, store):
        meta = SessionMeta(session_id="s_003", cwd="/proj", model_id="m")
        await store.init_session(meta)

        await store.update_meta("s_003", "/proj", {"prompt_count": 5, "total_tokens": 1000})

        loaded_meta, _ = await store.load_session("s_003", "/proj")
        assert loaded_meta.prompt_count == 5
        assert loaded_meta.total_tokens == 1000

    @pytest.mark.asyncio
    async def test_list_sessions(self, store):
        meta1 = SessionMeta(session_id="s_a", cwd="/a", model_id="m", title="First")
        meta2 = SessionMeta(session_id="s_b", cwd="/b", model_id="m", title="Second")
        await store.init_session(meta1)
        await store.init_session(meta2)

        sessions = store.list_sessions()
        assert len(sessions) >= 2

    @pytest.mark.asyncio
    async def test_find_latest(self, store):
        meta = SessionMeta(session_id="s_latest", cwd="/findme", model_id="m")
        await store.init_session(meta)

        import time
        time.sleep(0.01)  # Ensure timestamp difference

        latest = store.find_latest("/findme")
        assert latest is not None
        assert latest.session_id == "s_latest"

    @pytest.mark.asyncio
    async def test_find_latest_nonexistent(self, store):
        assert store.find_latest("/nowhere") is None

    @pytest.mark.asyncio
    async def test_save_full_history(self, store):
        meta = SessionMeta(session_id="s_004", cwd="/proj", model_id="m")
        await store.init_session(meta)

        await store.save_full_history("s_004", "/proj", [
            {"role": "system", "content": "compacted"},
            {"role": "user", "content": "summary"},
        ])

        _, messages = await store.load_session("s_004", "/proj")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_torn_tail_repair(self, store):
        """Append should repair a file that's missing a trailing newline."""
        meta = SessionMeta(session_id="s_torn", cwd="/proj", model_id="m")
        await store.init_session(meta)

        # Corrupt the file: write partial line without newline
        session_dir = store._session_dir("/proj", "s_torn")
        history_path = session_dir / "chat_history.jsonl"
        history_path.write_bytes(b'{"role":"user","content":"partial"}')

        # Append should repair and add new message
        await store.append_message("s_torn", "/proj", {"role": "assistant", "content": "fixed"})

        _, messages = await store.load_session("s_torn", "/proj")
        assert len(messages) == 2
        assert messages[0]["content"] == "partial"
        assert messages[1]["content"] == "fixed"


class TestDeltaIterator:
    @pytest.mark.asyncio
    async def test_basic_iteration(self, tmp_path):
        path = tmp_path / "test.jsonl"
        path.write_text(
            '{"a":1}\n{"b":2}\n{"c":3}\n',
            encoding="utf-8",
        )

        it = DeltaIterator(path, offset=0)
        results = []
        async for msg in it:
            results.append(msg)

        assert len(results) == 3
        assert results[0] == {"a": 1}
        assert results[1] == {"b": 2}
        assert results[2] == {"c": 3}

    @pytest.mark.asyncio
    async def test_offset_skip(self, tmp_path):
        path = tmp_path / "test.jsonl"
        path.write_text(
            '{"a":1}\n{"b":2}\n{"c":3}\n',
            encoding="utf-8",
        )

        # Skip first line (~9 bytes for '{"a":1}\n')
        offset = len(b'{"a":1}\n')
        it = DeltaIterator(path, offset=offset)
        results = []
        async for msg in it:
            results.append(msg)

        assert len(results) == 2
        assert results[0] == {"b": 2}
        assert results[1] == {"c": 3}

    @pytest.mark.asyncio
    async def test_empty_file(self, tmp_path):
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")

        it = DeltaIterator(path)
        results = []
        async for msg in it:
            results.append(msg)

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path):
        it = DeltaIterator(tmp_path / "nonexistent.jsonl")
        results = []
        async for msg in it:
            results.append(msg)
        assert len(results) == 0
