"""SQLite + FTS5 memory chunk index.

References:
    - Rust MemoryIndex (memory/src/index.rs)

Design:
    - aiosqlite for async database access
    - FTS5 contentless table: data in chunks, FTS stores index tokens only (~50% storage savings)
    - blake3/sha256 hash for change detection: only re-chunk when content changes
    - Windows FTS5 fallback: auto-detect FTS5 availability, degrade to LIKE queries
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from code_agent.memory.chunker import chunk_markdown

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS chunks (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,
    path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    text TEXT NOT NULL,
    hash TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    access_count INTEGER DEFAULT 0,
    last_accessed INTEGER
);

CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(hash);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='');
"""


class MemoryIndex:
    """SQLite + FTS5 memory chunk index.

    Database: ~/.code-agent/memory/{project_hash}/index.db
    """

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db: "aiosqlite.Connection | None" = None
        self._fts5_available: bool = False

    # ── Lifecycle ───────────────────────────────────────────────────────────

    async def open(self) -> None:
        """Open the database and create tables if needed."""
        import aiosqlite

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row

        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")

        # Run DDL (FTS5 table creation will silently fail if unsupported)
        try:
            await self._db.executescript(_DDL)
            await self._db.commit()
        except Exception as exc:
            logger.warning("DDL execution had issues (may lack FTS5): %s", exc)

        # Detect FTS5 via compile options (non-invasive, no table creation)
        self._fts5_available = await self._detect_fts5()
        if not self._fts5_available:
            logger.warning(
                "FTS5 not available in this SQLite build. "
                "Falling back to LIKE queries. "
                "Full-text search performance will be reduced."
            )

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ── Indexing ────────────────────────────────────────────────────────────

    async def reindex_file(
        self, path: Path, source: str = "workspace"
    ) -> int:
        """Index a MEMORY.md file by chunking it and upserting chunks."""
        if self._db is None:
            raise RuntimeError("MemoryIndex not opened. Call open() first.")

        if not path.exists():
            return 0

        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return 0

        chunks = chunk_markdown(text, str(path))
        now = int(time.time())
        count = 0

        for chunk in chunks:
            # Check if chunk exists and hash matches
            cursor = await self._db.execute(
                "SELECT rowid, hash FROM chunks WHERE id = ?", (chunk.id,)
            )
            row = await cursor.fetchone()

            if row and row["hash"] == chunk.hash:
                continue  # Hash unchanged, skip

            # Get old rowid for FTS5 cleanup (before upsert)
            old_rowid = row["rowid"] if row else None

            # Upsert chunk
            await self._db.execute(
                """INSERT OR REPLACE INTO chunks
                   (id, path, start_line, end_line, text, hash, source,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?,
                           COALESCE((SELECT created_at FROM chunks WHERE id = ?), ?),
                           ?)""",
                (chunk.id, str(path), chunk.start_line, chunk.end_line,
                 chunk.text, chunk.hash, source, chunk.id, now, now),
            )

            # Update FTS5 index
            if self._fts5_available:
                try:
                    # Get new rowid
                    cur = await self._db.execute(
                        "SELECT rowid FROM chunks WHERE id = ?", (chunk.id,)
                    )
                    new_row = await cur.fetchone()
                    if new_row:
                        new_rowid = new_row["rowid"]
                        # Delete old FTS entry if existed
                        if old_rowid:
                            await self._db.execute(
                                "INSERT INTO chunks_fts(chunks_fts, rowid, text) "
                                "VALUES('delete', ?, '')",
                                (old_rowid,),
                            )
                        # Insert new FTS entry
                        await self._db.execute(
                            "INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)",
                            (new_rowid, chunk.text),
                        )
                except Exception as exc:
                    logger.debug("FTS5 index update failed (non-fatal): %s", exc)

            count += 1

        if count > 0:
            await self._db.commit()
            logger.info(
                "Indexed %s: %d chunks added/updated (%d total)",
                path, count, await self.get_chunk_count(),
            )

        return count

    # ── Search ──────────────────────────────────────────────────────────────

    async def search_fts(
        self, query: str, limit: int = 20
    ) -> list[dict]:
        """FTS5 BM25 keyword search. Degrades to LIKE when FTS5 unavailable."""
        if self._db is None:
            raise RuntimeError("MemoryIndex not opened. Call open() first.")

        if self._fts5_available:
            try:
                return await self._search_fts5(query, limit)
            except Exception as exc:
                logger.debug("FTS5 search failed, falling back to LIKE: %s", exc)

        return await self._search_like(query, limit)

    async def _search_fts5(self, query: str, limit: int) -> list[dict]:
        """FTS5 BM25 full-text search."""
        safe_query = _sanitize_fts5_query(query)

        cursor = await self._db.execute(
            """SELECT c.rowid, c.path, c.text, c.source, rank as score
               FROM chunks_fts f
               JOIN chunks c ON c.rowid = f.rowid
               WHERE chunks_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (safe_query, limit),
        )
        rows = await cursor.fetchall()

        # Update access stats
        now = int(time.time())
        for row in rows:
            await self._db.execute(
                "UPDATE chunks SET access_count = access_count + 1, "
                "last_accessed = ? WHERE rowid = ?",
                (now, row["rowid"]),
            )
        if rows:
            await self._db.commit()

        return [dict(row) for row in rows]

    async def _search_like(self, query: str, limit: int) -> list[dict]:
        """LIKE fallback."""
        cursor = await self._db.execute(
            """SELECT rowid, path, text, source, 0.0 as score
               FROM chunks WHERE text LIKE ? ORDER BY rowid DESC LIMIT ?""",
            (f"%{query}%", limit),
        )
        return [dict(row) for row in await cursor.fetchall()]

    # ── Maintenance ─────────────────────────────────────────────────────────

    async def delete_chunks_for_file(self, path: Path) -> int:
        """Remove all chunks belonging to a file."""
        if self._db is None:
            raise RuntimeError("MemoryIndex not opened. Call open() first.")

        # Get rowids for FTS5 cleanup
        cursor = await self._db.execute(
            "SELECT rowid FROM chunks WHERE path = ?", (str(path),)
        )
        rowids = [row["rowid"] for row in await cursor.fetchall()]

        # Delete from FTS5 first
        if rowids and self._fts5_available:
            for rid in rowids:
                try:
                    await self._db.execute(
                        "INSERT INTO chunks_fts(chunks_fts, rowid, text) "
                        "VALUES('delete', ?, '')",
                        (rid,),
                    )
                except Exception as exc:
                    logger.debug("FTS5 delete failed (non-fatal): %s", exc)

        # Delete from chunks
        cursor = await self._db.execute(
            "DELETE FROM chunks WHERE path = ?", (str(path),)
        )
        await self._db.commit()

        count = cursor.rowcount
        if count > 0:
            logger.debug("Deleted %d chunks for %s", count, path)

        return count

    async def get_chunk_count(self) -> int:
        """Total number of indexed chunks."""
        if self._db is None:
            return 0
        cursor = await self._db.execute("SELECT COUNT(*) as cnt FROM chunks")
        row = await cursor.fetchone()
        return row["cnt"] if row else 0

    # ── Internal ────────────────────────────────────────────────────────────

    async def _detect_fts5(self) -> bool:
        """Detect whether the SQLite build supports FTS5.

        Uses PRAGMA compile_options (non-invasive, no table creation).
        """
        if self._db is None:
            return False
        try:
            cursor = await self._db.execute("PRAGMA compile_options")
            options = [row[0] for row in await cursor.fetchall()]
            return "ENABLE_FTS5" in options
        except Exception:
            return False


def _sanitize_fts5_query(query: str) -> str:
    """Sanitize a user query for FTS5 MATCH.

    Removes special characters and wraps terms in double-quotes.
    """
    for char in r'*"():^-':
        query = query.replace(char, " ")
    terms = [t.strip() for t in query.split() if t.strip()]
    if not terms:
        return '""'
    return " OR ".join(f'"{t}"' for t in terms)
