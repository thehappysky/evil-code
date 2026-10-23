"""JSONL session persistence with append + delta replay.

References:
    - Rust JsonlStorageAdapter (storage/jsonl/mod.rs)
    - Rust UpdatesIterator (storage/mod.rs:26)

Design:
    - Append-first architecture: O(1) writes, no transactions
    - Crash-safe: torn-tail repair on read, temp+fsync+replace for summary.json
    - Delta Replay: SeekFrom::Start(offset) enables efficient resume
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import uuid
from base64 import urlsafe_b64encode
from datetime import datetime, timezone
from itertools import chain
from pathlib import Path

from code_agent.storage.file_lock import FileLock
from code_agent.storage.models import SessionMeta

logger = logging.getLogger(__name__)

# Default root directory for session storage
DEFAULT_SESSIONS_DIR = Path.home() / ".code-agent" / "sessions"


def _encode_cwd(cwd: str | Path) -> str:
    """Encode a working directory path for use in directory names.

    Uses URL-safe base64 to avoid filesystem-incompatible characters.
    """
    raw = str(cwd).encode("utf-8")
    return urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _generate_session_id() -> str:
    """Generate a unique session ID.

    Format: timestamp prefix + UUID (e.g. "s_20260725_a1b2c3d4")
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    short_uuid = uuid.uuid4().hex[:8]
    return f"s_{ts}_{short_uuid}"


# ─── SessionStore ────────────────────────────────────────────────────────────────


class SessionStore:
    """JSONL session persistence.

    Directory layout::

        ~/.code-agent/sessions/{workspace_hash}/{session_id}/
            chat_history.jsonl   -- One ConversationItem JSON per line
            summary.json         -- SessionMeta metadata

    Usage::

        store = SessionStore()
        meta = SessionMeta(session_id="s_001", cwd="/project", model_id="claude")
        await store.init_session(meta)
        await store.append_message("s_001", {"role": "user", "content": "hello"})
        loaded_meta, messages = await store.load_session("s_001")
    """

    def __init__(self, root_dir: str | Path | None = None):
        self._root = Path(root_dir) if root_dir else DEFAULT_SESSIONS_DIR

    # ── Write operations ────────────────────────────────────────────────────

    async def init_session(self, meta: SessionMeta) -> None:
        """Create session directory and write initial summary.json.

        Args:
            meta: Session metadata to persist as summary.json.
        """
        session_dir = self._session_dir(meta.cwd, meta.session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        # Write empty chat_history.jsonl to create the file
        history_path = session_dir / "chat_history.jsonl"
        if not history_path.exists():
            history_path.write_text("", encoding="utf-8")

        # Atomic write of summary.json
        await self.update_meta(meta.session_id, meta.cwd, meta.model_dump(mode="json"))

    async def append_message(
        self, session_id: str, cwd: str, message: dict
    ) -> None:
        """Append a single message as one JSON line to chat_history.jsonl.

        Performs torn-tail repair: if last byte is not newline, appends a
        newline before writing the new message. This handles crash recovery.

        Args:
            session_id: Session identifier.
            cwd: Working directory (needed to locate the session directory).
            message: Dictionary to serialize as JSON line.
        """
        history_path = self._session_dir(cwd, session_id) / "chat_history.jsonl"

        # Torn-tail repair: ensure file ends with newline if non-empty
        try:
            if history_path.exists():
                with open(history_path, "ab+") as f:
                    f.seek(0, os.SEEK_END)
                    pos = f.tell()
                    if pos > 0:
                        f.seek(pos - 1)
                        last_byte = f.read(1)
                        if last_byte != b"\n":
                            f.seek(0, os.SEEK_END)
                            f.write(b"\n")
        except OSError as exc:
            logger.warning("Torn-tail check failed for %s: %s", history_path, exc)

        # Append the new message
        line = json.dumps(message, ensure_ascii=False) + "\n"
        try:
            with open(history_path, "ab") as f:
                f.write(line.encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
        except OSError as exc:
            logger.error("Failed to append message to %s: %s", history_path, exc)
            raise

    async def update_meta(
        self, session_id: str, cwd: str, patch: dict
    ) -> None:
        """Atomic update of summary.json using file lock + temp file.

        Args:
            session_id: Session identifier.
            cwd: Working directory.
            patch: Dictionary of fields to merge into the existing meta.
        """
        session_dir = self._session_dir(cwd, session_id)
        summary_path = session_dir / "summary.json"

        async with FileLock(summary_path) as _lock:
            # Read existing meta, merge patch, write atomically
            existing: dict = {}
            if summary_path.exists():
                try:
                    existing = json.loads(summary_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    existing = {}

            existing.update(patch)
            existing["updated_at"] = datetime.now(timezone.utc).isoformat()

            # Atomic write: temp file -> fsync -> replace
            tmp_path = summary_path.with_suffix(".tmp")
            try:
                content = json.dumps(existing, ensure_ascii=False, indent=2)
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(str(tmp_path), str(summary_path))
            except OSError:
                try:
                    os.unlink(str(tmp_path))
                except OSError:
                    pass
                raise

    async def save_full_history(
        self, session_id: str, cwd: str, messages: list[dict]
    ) -> None:
        """Overwrite the entire chat_history.jsonl (used after compaction).

        Uses atomic write pattern: temp file -> fsync -> replace.

        Args:
            session_id: Session identifier.
            cwd: Working directory.
            messages: Complete list of message dicts to persist.
        """
        history_path = self._session_dir(cwd, session_id) / "chat_history.jsonl"
        session_dir = history_path.parent
        session_dir.mkdir(parents=True, exist_ok=True)

        content = "\n".join(
            json.dumps(m, ensure_ascii=False) for m in messages
        ) + "\n"

        tmp_path = history_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp_path), str(history_path))
        except OSError:
            try:
                os.unlink(str(tmp_path))
            except OSError:
                pass
            raise

    # ── Read operations ─────────────────────────────────────────────────────

    async def load_session(
        self, session_id: str, cwd: str
    ) -> tuple[SessionMeta | None, list[dict]]:
        """Load full session: summary.json + chat_history.jsonl.

        Args:
            session_id: Session identifier.
            cwd: Working directory.

        Returns:
            Tuple of (SessionMeta or None if not found, list of message dicts).

            Returns (None, []) if the session directory doesn't exist.
        """
        session_dir = self._session_dir(cwd, session_id)
        if not session_dir.exists():
            return None, []

        # Load summary.json
        meta: SessionMeta | None = None
        summary_path = session_dir / "summary.json"
        if summary_path.exists():
            try:
                data = json.loads(summary_path.read_text(encoding="utf-8"))
                meta = SessionMeta.model_validate(data)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("Corrupt summary.json for %s: %s", session_id, exc)

        # Load chat_history.jsonl
        messages = await self._read_history_lines(session_id, cwd)

        return meta, messages

    async def load_delta(
        self, session_id: str, cwd: str, offset: int = 0
    ) -> list[dict]:
        """Load only messages appended after a known byte offset.

        Args:
            session_id: Session identifier.
            cwd: Working directory.
            offset: Byte offset to seek to before reading.

        Returns:
            List of message dicts at or after the given offset.
        """
        delta = DeltaIterator(
            self._session_dir(cwd, session_id) / "chat_history.jsonl",
            offset=offset,
        )
        return [msg async for msg in delta]

    def list_sessions(self) -> list[SessionMeta]:
        """List all saved sessions sorted by last update time (newest first).

        Returns:
            List of SessionMeta, sorted by updated_at descending.
        """
        result: list[SessionMeta] = []

        if not self._root.exists():
            return result

        for cwd_dir in self._root.iterdir():
            if not cwd_dir.is_dir():
                continue
            for session_dir in cwd_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                summary_path = session_dir / "summary.json"
                if summary_path.exists():
                    try:
                        data = json.loads(
                            summary_path.read_text(encoding="utf-8")
                        )
                        meta = SessionMeta.model_validate(data)
                        result.append(meta)
                    except (json.JSONDecodeError, ValueError) as exc:
                        logger.debug(
                            "Skipping corrupt session %s: %s",
                            session_dir.name,
                            exc,
                        )

        result.sort(key=lambda m: m.updated_at, reverse=True)
        return result

    def find_latest(self, cwd: str) -> SessionMeta | None:
        """Find most recent session for a given working directory.

        Args:
            cwd: Working directory to search for.

        Returns:
            SessionMeta of the most recently updated session, or None.
        """
        best: SessionMeta | None = None
        for session_dir in chain.from_iterable(
            directory.iterdir() for directory in self._cwd_dirs(cwd)
            if directory.is_dir()
        ):
            if not session_dir.is_dir():
                continue
            summary_path = session_dir / "summary.json"
            if summary_path.exists():
                try:
                    data = json.loads(
                        summary_path.read_text(encoding="utf-8")
                    )
                    meta = SessionMeta.model_validate(data)
                    if best is None or meta.updated_at > best.updated_at:
                        best = meta
                except (json.JSONDecodeError, ValueError) as exc:
                    logger.debug(
                        "Skipping corrupt session %s: %s",
                        session_dir.name,
                        exc,
                    )

        return best

    async def get_history_byte_offset(
        self, session_id: str, cwd: str
    ) -> int:
        """Get the current byte size of chat_history.jsonl.

        Used by PersistenceHook to track delta read positions.

        Args:
            session_id: Session identifier.
            cwd: Working directory.

        Returns:
            Current file size in bytes, or 0 if the file doesn't exist.
        """
        history_path = self._session_dir(cwd, session_id) / "chat_history.jsonl"
        try:
            return history_path.stat().st_size
        except OSError:
            return 0

    # ── Internal helpers ────────────────────────────────────────────────────

    def _session_dir(self, cwd: str, session_id: str) -> Path:
        """Use bounded paths for new sessions; keep old sessions readable."""
        directories = self._cwd_dirs(cwd)
        for directory in directories:
            candidate = directory / session_id
            if candidate.is_dir():
                return candidate
        return directories[0] / session_id

    def _cwd_dirs(self, cwd: str) -> list[Path]:
        """Current workspace directory followed by the legacy Base64 layout."""
        normalized = os.path.normcase(os.path.realpath(os.path.expanduser(cwd)))
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
        directories = [self._root / ("p_" + digest)]
        legacy = _encode_cwd(cwd)
        # A component longer than 255 bytes could never have been created on
        # supported filesystems. Avoid probing it (Windows raises WinError 206).
        if len(legacy) <= 255:
            legacy_dir = self._root / legacy
            try:
                if legacy_dir.is_dir():
                    directories.append(legacy_dir)
            except OSError:
                pass
        return directories

    async def _read_history_lines(
        self, session_id: str, cwd: str
    ) -> list[dict]:
        """Read all JSON lines from chat_history.jsonl.

        Handles torn-tail: if the last line is incomplete (no trailing newline,
        or invalid JSON), it is silently discarded.
        """
        history_path = self._session_dir(cwd, session_id) / "chat_history.jsonl"

        if not history_path.exists():
            return []

        try:
            content = history_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read %s: %s", history_path, exc)
            return []

        messages: list[dict] = []
        for line in content.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                # Torn-tail: last line might be truncated
                logger.debug(
                    "Skipping invalid JSON line in %s (likely torn-tail)",
                    history_path,
                )

        return messages


# ─── DeltaIterator ───────────────────────────────────────────────────────────────


class DeltaIterator:
    """Line-by-line JSONL reader with byte offset tracking.

    Equivalent to Rust's UpdatesIterator.
    Uses buffered reading + seek(offset) for efficient partial reads.

    Usage::

        it = DeltaIterator(path, offset=1024)
        async for msg in it:
            print(msg)
        print(f"Next read offset: {it.current_offset}")
    """

    def __init__(self, path: Path, offset: int = 0):
        self._path = path
        self._offset = offset
        self._current_offset = offset

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict:
        import aiofiles

        if not self._path.exists():
            raise StopAsyncIteration

        async with aiofiles.open(self._path, "rb") as f:
            await f.seek(self._current_offset)

            # Read lines from current position
            line_bytes = await f.readline()
            if not line_bytes:
                raise StopAsyncIteration

            self._current_offset = await f.tell()
            line = line_bytes.decode("utf-8").strip()

            if not line:
                # Empty line, try next one
                return await self.__anext__()

            try:
                return json.loads(line)
            except json.JSONDecodeError:
                logger.debug(
                    "Invalid JSON line at offset %d in %s",
                    self._offset,
                    self._path,
                )
                # Skip invalid line, try next
                return await self.__anext__()

    @property
    def current_offset(self) -> int:
        """Current byte position.

        Used to save checkpoint for next delta read.
        """
        return self._current_offset
