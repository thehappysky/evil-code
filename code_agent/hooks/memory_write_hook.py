"""MemoryWriteHook — the cross-session memory WRITE path.

References:
    - Rust MemoryStore / memory index (xai-grok-memory)

Previously the memory subsystem was read-only in practice: ``MemoryHook``
searched the SQLite/FTS5 index, but nothing ever indexed session content, so
the index stayed empty. This hook closes that gap:

- On ``on_session_end`` it appends the session conversation to a per-workspace
  session log (``{memory_root}/{project_hash}/sessions/*.md``).
- It then reindexes the session log (source="session") plus any hand-written
  workspace/global ``MEMORY.md`` (source="workspace"/"global") into the
  SQLite/FTS5 index, so future ``MemoryHook`` searches can retrieve them.
  Each run reindexes exactly the log file that was just appended (O(1) per
  run) — a "last N files" window would miss sessions whose filenames sort
  early lexicographically.

All failures degrade gracefully — memory persistence is best-effort.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from code_agent.core.session import Session
    from code_agent.memory.file_store import MemoryStore
    from code_agent.memory.index_db import MemoryIndex

logger = logging.getLogger(__name__)


def _session_to_markdown(messages: list) -> str:
    """Render session messages as a Markdown memory log (H2-sectioned).

    H2 sections match the memory chunker's primary split boundary, so each
    role block becomes one searchable chunk. The system prompt is excluded —
    it is static context, not memory.
    """
    lines: list[str] = []
    for msg in messages:
        role = getattr(msg, "role", "?")
        if role == "system":
            continue

        content = getattr(msg, "content", None)
        if role == "tool":
            call_id = getattr(msg, "tool_call_id", "")
            content = f"[tool_result call_id={call_id}] {content or ''}"

        if content:
            lines.append(f"## {role}\n\n{content}")

        for tc in getattr(msg, "tool_calls", None) or []:
            lines.append(f"## assistant tool_call: {tc.name}\n\n{tc.arguments}")

    return "\n\n".join(lines)


class MemoryWriteHook:
    """Persist each run's conversation into the cross-session memory index."""

    def __init__(
        self,
        memory_index: "MemoryIndex",
        memory_store: "MemoryStore",
        cwd: str,
    ):
        self._index = memory_index
        self._store = memory_store
        self._cwd = str(cwd)
        self._session_id: str | None = None
        self._first_user = ""

    async def on_session_start(self, session: "Session") -> None:
        """Capture session identity + the real first user prompt (once)."""
        try:
            if self._session_id is None:
                self._session_id = getattr(session, "_session_id", None)

            if not self._first_user:
                # The LAST user message at session start is the actual prompt
                # (earlier user messages may be synthetic skills/project lists).
                for msg in reversed(session.messages):
                    if getattr(msg, "role", "") == "user":
                        self._first_user = msg.content
                        break
        except Exception:
            logger.warning(
                "MemoryWriteHook.on_session_start failed", exc_info=True,
            )

    async def on_session_end(
        self, session: "Session", reason: str = "completed",
    ) -> None:
        """Write the session log and reindex it into memory."""
        try:
            content = _session_to_markdown(session.messages)
            if not content.strip():
                return

            sid = (
                self._session_id
                or getattr(session, "_session_id", None)
                or "unknown"
            )
            log_path = await self._store.append_session_log(
                self._cwd, sid, self._first_user or "session", content,
            )
            if log_path is not None:
                await self._index.reindex_file(log_path, source="session")

            # Also pick up hand-written MEMORY.md files.
            global_md = self._store.root_dir / "MEMORY.md"
            if global_md.exists():
                await self._index.reindex_file(global_md, source="global")
            workspace_md = self._store.workspace_dir(self._cwd) / "MEMORY.md"
            if workspace_md.exists():
                await self._index.reindex_file(workspace_md, source="workspace")
        except Exception:
            logger.warning(
                "Memory write failed; continuing without memory persistence",
                exc_info=True,
            )
