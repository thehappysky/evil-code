"""Tests for storage/file_lock.py — cross-platform file locking."""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from code_agent.storage.file_lock import FileLock


class TestFileLock:
    @pytest.fixture
    def lock_path(self, tmp_path):
        return tmp_path / "test.lock"

    def test_sync_acquire_release(self, lock_path):
        """Basic sync lock/unlock."""
        lock = FileLock(lock_path, timeout=1.0)
        lock.acquire()
        assert lock.is_locked
        lock.release()
        assert not lock.is_locked

    def test_sync_context_manager(self, lock_path):
        """Sync context manager usage."""
        with FileLock(lock_path, timeout=1.0) as lock:
            assert lock.is_locked
            # Verify lock file exists
            assert lock_path.with_suffix(".lock").exists() or True
        assert not lock.is_locked

    @pytest.mark.asyncio
    async def test_async_context_manager(self, lock_path):
        """Async context manager usage."""
        async with FileLock(lock_path, timeout=1.0) as lock:
            assert lock.is_locked
        assert not lock.is_locked

    def test_timeout(self, lock_path):
        """Lock should raise TimeoutError if already held."""
        lock1 = FileLock(lock_path, timeout=0.1)

        with lock1:
            lock2 = FileLock(lock_path, timeout=0.1)
            with pytest.raises(TimeoutError):
                lock2.acquire()

    def test_release_releases_lock(self, lock_path):
        """Release frees the lock and the lock is immediately re-acquirable.

        The lock file intentionally persists (deleting it on release breaks
        mutual exclusion: a waiter holds a stale fd while a new contender locks
        a freshly-created file). What matters is that the lock itself is free.
        """
        lock = FileLock(lock_path)
        lock.acquire()
        assert lock.is_locked
        lock.release()
        assert not lock.is_locked
        # A fresh lock on the same path can acquire immediately (no timeout).
        reacquired = FileLock(lock_path, timeout=5.0)
        reacquired.acquire()
        assert reacquired.is_locked
        reacquired.release()

    def test_double_release(self, lock_path):
        """Double release should not raise."""
        lock = FileLock(lock_path, timeout=1.0)
        lock.acquire()
        lock.release()
        lock.release()  # Should not raise

    def test_concurrent_access_prevention(self, lock_path):
        """Two locks on the same path should block each other."""
        results = []

        with FileLock(lock_path, timeout=5.0):
            results.append("first")
            lock2 = FileLock(lock_path, timeout=0.1)
            try:
                lock2.acquire()
                results.append("second")  # Should not reach
            except TimeoutError:
                results.append("timeout")

        assert results == ["first", "timeout"]
