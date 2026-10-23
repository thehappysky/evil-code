"""MEMORY.md file I/O and session log management.

References:
    - Rust MemoryStore equivalent

Directory layout::

    ~/.code-agent/memory/
        MEMORY.md                  -- global memory (user preferences/habits)
        {project_hash}/            -- per-workspace memory directory
            MEMORY.md              -- project-level memory
            index.db               -- SQLite search index
            sessions/              -- auto-generated session logs
                YYYY-MM-DD-{slug}-{session_id_prefix}.md
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from code_agent.helpers.file_io import atomic_write

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_DIR = Path.home() / ".code-agent" / "memory"


def _project_hash(cwd: str | Path) -> str:
    """Generate a stable project hash from a working directory.

    Uses SHA256 of the resolved path (first 12 hex chars).
    """
    resolved = str(Path(cwd).resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]


class MemoryStore:
    """MEMORY.md file I/O and session log management.

    Usage::

        store = MemoryStore()
        global_md = await store.read_global()
        project_md = await store.read_workspace("/project")
        await store.write_workspace("/project", "# Project Memory\\n\\n...")
        await store.append_session_log("/project", "s_001", "hello", "...")
    """

    def __init__(self, root_dir: Path | None = None):
        self._root = Path(root_dir) if root_dir else DEFAULT_MEMORY_DIR

    @property
    def root_dir(self) -> Path:
        """The memory root directory (~/.code-agent/memory by default)."""
        return self._root

    # ── Read operations ─────────────────────────────────────────────────────

    async def read_global(self) -> str:
        """Read global MEMORY.md content.

        Returns:
            Content string, or empty string if file doesn't exist.
        """
        global_path = self._root / "MEMORY.md"
        if not global_path.exists():
            return ""
        try:
            return global_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read global MEMORY.md: %s", exc)
            return ""

    async def read_workspace(self, cwd: Path | str) -> str:
        """Read per-project MEMORY.md content.

        Project identity: SHA256 hash of canonicalized cwd path.

        Args:
            cwd: Working directory.

        Returns:
            Content string, or empty string if file doesn't exist.
        """
        ws_path = self._workspace_memory_path(cwd)
        if not ws_path.exists():
            return ""
        try:
            return ws_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read workspace MEMORY.md: %s", exc)
            return ""

    # ── Write operations ────────────────────────────────────────────────────

    async def write_workspace(
        self, cwd: Path | str, content: str
    ) -> None:
        """Write per-project MEMORY.md (atomic).

        Args:
            cwd: Working directory.
            content: Markdown content to write.
        """
        ws_path = self._workspace_memory_path(cwd)
        ws_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(ws_path, content)

    async def write_global(self, content: str) -> None:
        """Write global MEMORY.md (atomic).

        Args:
            content: Markdown content to write.
        """
        global_path = self._root / "MEMORY.md"
        self._root.mkdir(parents=True, exist_ok=True)
        atomic_write(global_path, content)

    # ── Session logs ────────────────────────────────────────────────────────

    async def append_session_log(
        self,
        cwd: Path | str,
        session_id: str,
        first_message: str,
        content: str,
    ) -> Path | None:
        """Append to daily session log file.

        Format: {date}-{slug}-{sid_prefix}.md
        Each append is prefixed with <!-- flush HH:MM:SS UTC --> timestamp.

        Args:
            cwd: Working directory.
            session_id: Session identifier.
            first_message: First user message (used for slug generation).
            content: Session log content to append.

        Returns:
            The log file path, or None if the append failed.
        """
        sessions_dir = self._workspace_memory_path(cwd).parent / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        # Build filename
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        slug = _make_slug(first_message, max_len=30)
        sid_prefix = session_id[:12] if len(session_id) >= 12 else session_id
        filename = f"{today}-{slug}-{sid_prefix}.md"
        log_path = sessions_dir / filename

        # Build entry with timestamp comment
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        entry = f"\n<!-- flush {ts} UTC -->\n{content}\n"

        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(entry)
                f.flush()
                os.fsync(f.fileno())
        except OSError as exc:
            logger.warning("Failed to append session log %s: %s", log_path, exc)
            return None
        return log_path

    # ── Internal ────────────────────────────────────────────────────────────

    def _workspace_memory_path(self, cwd: Path | str) -> Path:
        """Get the per-workspace MEMORY.md path."""
        return self._root / _project_hash(cwd) / "MEMORY.md"

    def workspace_dir(self, cwd: Path | str) -> Path:
        """Get the per-workspace memory directory."""
        return self._root / _project_hash(cwd)


def _make_slug(text: str, max_len: int = 30) -> str:
    """Create a filename-safe slug from text."""
    slug = ""
    for ch in text.lower():
        if ch.isalnum() or ch in " -_":
            slug += ch if ch != " " else "-"
        if len(slug) >= max_len:
            break
    slug = slug.strip("-").rstrip("-")
    return slug[:max_len] if slug else "session"
