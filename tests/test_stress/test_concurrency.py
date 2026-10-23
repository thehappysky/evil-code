"""Stress/concurrency tests for file locking and session persistence.

These tests exercise REAL contention on ``FileLock`` and ``SessionStore``
from multiple threads / asyncio tasks. They are deliberately heavier than
the unit tests in tests/test_storage/ and are meant to catch races that a
single-threaded test never sees:

- FileLock held by several OS threads (sync) and several asyncio tasks
- ``append_message`` / ``update_meta`` called concurrently on one session
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from code_agent.storage.file_lock import FileLock
from code_agent.storage.models import SessionMeta
from code_agent.storage.session_store import SessionStore


class TestFileLockRealContention:
    """Several OS threads contend for the same FileLock."""

    def test_filelock_real_thread_contention(self, tmp_path: Path) -> None:
        """Several OS threads each increment a shared counter inside the same
        ``FileLock`` critical section; no increment may be lost.

        Regression for the release()-unlink bug: ``FileLock.release()`` used to
        unlink the lock file, leaving waiters on stale fds while new contenders
        locked a freshly-created file — two groups could both hold "the lock".
        Fixed by keeping the lock file. 60/60 trials pass on Windows.
        """
        lock_path = tmp_path / "contended.lock"
        thread_count = 16
        counter = 0
        barrier = threading.Barrier(thread_count)

        def worker() -> None:
            nonlocal counter
            barrier.wait()
            with FileLock(lock_path, timeout=10.0):
                c = counter
                counter = c + 1

        threads = [threading.Thread(target=worker) for _ in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert counter == thread_count

    async def test_async_filelock_contention(self, tmp_path: Path) -> None:
        """Multiple asyncio tasks contend for the same FileLock.

        Each task runs ``async with FileLock(...)`` (which wraps the sync
        acquire in ``asyncio.to_thread``) and increments a shared counter
        inside the critical section. If exclusive access holds, the final
        counter equals the number of tasks.
        """
        lock_path = tmp_path / "contended.lock"
        counter = 0

        async def worker() -> None:
            nonlocal counter
            async with FileLock(lock_path, timeout=10.0):
                current = counter
                await asyncio.sleep(0.0005)  # widen critical section
                counter = current + 1

        task_count = 5
        await asyncio.gather(*(worker() for _ in range(task_count)))

        assert counter == task_count, (
            f"expected {task_count} increments, got {counter} "
            f"(mutual exclusion broken?)"
        )


class TestConcurrentAppendMessage:
    """Concurrent appends to the same session JSONL file."""

    async def test_concurrent_append_message(self, tmp_path: Path) -> None:
        """20 concurrent append_message calls must all survive a load_session.

        ``append_message`` is append-only (O_APPEND) and does not take a lock;
        this asserts that concurrent writers still produce a parseable JSONL
        file containing every message exactly once.
        """
        store = SessionStore(root_dir=tmp_path)
        sid, cwd = "s_append", "/proj"
        await store.init_session(SessionMeta(session_id=sid, cwd=cwd, model_id="m"))

        message_count = 20

        async def append(idx: int) -> None:
            await store.append_message(
                sid,
                cwd,
                {"role": "user", "content": f"concurrent message {idx}", "idx": idx},
            )

        await asyncio.gather(*(append(i) for i in range(message_count)))

        # All messages must round-trip through load_session.
        loaded_meta, messages = await store.load_session(sid, cwd)
        assert loaded_meta is not None
        assert len(messages) == message_count
        idxs = {m.get("idx") for m in messages}
        assert idxs == set(range(message_count)), f"lost messages: {set(range(message_count)) - idxs}"

        # The JSONL file must be parseable line-by-line (no torn/interleaved lines).
        history_path = store._session_dir(cwd, sid) / "chat_history.jsonl"
        lines = [ln for ln in history_path.read_text(encoding="utf-8").split("\n") if ln.strip()]
        assert len(lines) == message_count
        for line in lines:
            json.loads(line)  # must not raise


class TestConcurrentUpdateMeta:
    """Concurrent summary.json updates through the FileLock."""

    async def test_concurrent_update_meta(self, tmp_path: Path) -> None:
        """Concurrent update_meta calls must never corrupt summary.json.

        Each call merges its own patch under the FileLock; whatever the final
        value, the persisted file must always round-trip through
        ``SessionMeta.model_validate`` (valid JSON, required fields present).
        """
        store = SessionStore(root_dir=tmp_path)
        sid, cwd = "s_meta", "/proj"
        await store.init_session(SessionMeta(session_id=sid, cwd=cwd, model_id="m"))

        update_count = 6

        async def bump(idx: int) -> None:
            await store.update_meta(sid, cwd, {"prompt_count": idx, "total_tokens": idx * 100})

        # Hard bound so a wedged lock can never hang the suite.
        await asyncio.wait_for(
            asyncio.gather(*(bump(i) for i in range(update_count))),
            timeout=25.0,
        )

        loaded_meta, _ = await store.load_session(sid, cwd)
        assert loaded_meta is not None, "summary.json failed model_validate after concurrent writes"
        assert isinstance(loaded_meta.prompt_count, int)
        assert isinstance(loaded_meta.total_tokens, int)
        # prompt_count and total_tokens are always written together in one
        # update_meta call; a torn read-modify-write would pair a prompt_count
        # from one write with a total_tokens from another.
        assert loaded_meta.prompt_count in range(update_count)
        assert loaded_meta.total_tokens == loaded_meta.prompt_count * 100, (
            f"write-pair torn: prompt_count={loaded_meta.prompt_count}, "
            f"total_tokens={loaded_meta.total_tokens}"
        )

        # summary.json itself must be valid JSON on disk.
        summary_path = store._session_dir(cwd, sid) / "summary.json"
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert data["session_id"] == sid
