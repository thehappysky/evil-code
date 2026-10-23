"""Cross-platform file locking.

Windows: msvcrt.locking
POSIX: fcntl.flock

Both sync and async context managers are provided.
Async versions wrap sync I/O via asyncio.to_thread to avoid
blocking the event loop with file lock operations.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Platform-specific lock implementation
if sys.platform == "win32":
    import msvcrt

    def _lock_file(fd: int) -> None:
        """Acquire exclusive lock on Windows."""
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)

    def _unlock_file(fd: int) -> None:
        """Release exclusive lock on Windows."""
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass  # Already unlocked or file closed

else:
    import fcntl

    def _lock_file(fd: int) -> None:
        """Acquire exclusive lock on POSIX."""
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _unlock_file(fd: int) -> None:
        """Release exclusive lock on POSIX."""
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass  # Already unlocked or file closed


class FileLock:
    """Cross-platform exclusive file lock.

    Usage (sync):
        with FileLock(path) as lock:
            # exclusive access
            ...

    Usage (async):
        async with FileLock(path) as lock:
            # exclusive access
            ...

    The lock file is created at ``{path}.lock`` in the same directory.
    Timeout is in seconds; on timeout raises TimeoutError.
    """

    def __init__(self, path: Path, timeout: float = 30.0):
        self._target_path = path
        self._lock_path = path.with_suffix(path.suffix + ".lock")
        self._timeout = timeout
        self._fd: int | None = None

    # ── Sync context manager ────────────────────────────────────────────────

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()

    # ── Async context manager ───────────────────────────────────────────────

    async def __aenter__(self) -> "FileLock":
        await asyncio.to_thread(self.acquire)
        return self

    async def __aexit__(self, *args: object) -> None:
        await asyncio.to_thread(self.release)

    # ── Core operations ─────────────────────────────────────────────────────

    def acquire(self) -> None:
        """Acquire the lock with timeout.

        Raises:
            TimeoutError: If lock cannot be acquired within timeout.
        """
        self._fd = os.open(
            str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o600
        )

        deadline = time.monotonic() + self._timeout
        while True:
            try:
                _lock_file(self._fd)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(self._fd)
                    self._fd = None
                    raise TimeoutError(
                        f"Failed to acquire lock on {self._lock_path} "
                        f"within {self._timeout}s"
                    )
                time.sleep(0.05)

    def release(self) -> None:
        """Release the lock.

        The lock file is intentionally NOT deleted. If a waiter is spinning on
        an open fd of the lock file and we unlink it, that fd becomes stale: a
        new contender that re-creates the path locks a *different* file, so two
        processes/threads can both hold "the lock" and mutual exclusion breaks
        (observed on Windows msvcrt). Keeping the file gives every contender the
        same inode to contend on. The zero-byte lock file is harmless and is
        reused on the next acquire.
        """
        if self._fd is not None:
            try:
                _unlock_file(self._fd)
                os.close(self._fd)
            except OSError as exc:
                logger.debug("Error releasing lock %s: %s", self._lock_path, exc)
            finally:
                self._fd = None

    @property
    def is_locked(self) -> bool:
        """Whether the lock is currently held."""
        return self._fd is not None
