# Phase 2 Module 2: memory/ — SQLite FTS5 Memory Index + Chunker + File Store

> Phase: 2 | Status: DONE | Created: 2026-07-25

---

## Scope

| Feature | Description |
|:---|:---|
| MemoryIndex | SQLite + FTS5 chunk index: open, reindex_file, search_fts, delete_chunks_for_file |
| chunk_markdown | Split Markdown by ## headers; oversize chunks split on paragraph boundaries |
| search_memory | Hybrid search pipeline: FTS5 -> filter empty -> weight by source -> truncate |
| MemoryStore | MEMORY.md file I/O + session log writing |

---

## Final Architecture

```
code_agent/memory/
├── __init__.py          # exports: MemoryIndex, MemoryStore, search_memory, chunk_markdown, Chunk, SearchResult
├── index_db.py          # SQLite + FTS5 index: open/close, reindex, search, delete (247 lines)
├── schema.sql           # DDL reference (chunks + chunks_fts virtual table)
├── chunker.py           # Markdown H2 chunking + oversized paragraph split (149 lines)
├── search.py            # Hybrid search pipeline: filter + weight + truncate (130 lines)
└── file_store.py        # MEMORY.md I/O + session log appending (156 lines)
```

---

## Implementation Status

| Item | Status | File |
|:---|:---|:---|
| Chunk dataclass (id, text, start_line, end_line, hash) | Done | `memory/chunker.py` |
| chunk_markdown() — H2 split + paragraph secondary split | Done | `memory/chunker.py` |
| _make_chunk() — blake3 hash (with hashlib fallback) | Done | `memory/chunker.py` |
| schema.sql DDL (chunks + chunks_fts contentless) | Done | `memory/schema.sql` |
| MemoryIndex — aiosqlite open/close with WAL mode | Done | `memory/index_db.py` |
| MemoryIndex.reindex_file() — chunk + hash check + upsert | Done | `memory/index_db.py` |
| MemoryIndex._detect_fts5() — PRAGMA compile_options (non-invasive) | Done | `memory/index_db.py` |
| MemoryIndex._search_fts5() — BM25 rank + access tracking | Done | `memory/index_db.py` |
| MemoryIndex._search_like() — LIKE fallback for no-FTS5 builds | Done | `memory/index_db.py` |
| MemoryIndex.delete_chunks_for_file() — FTS5 delete + chunks delete | Done | `memory/index_db.py` |
| _sanitize_fts5_query() — escape special chars, wrap terms in quotes | Done | `memory/index_db.py` |
| SearchResult dataclass (chunk_id, path, text, source, score) | Done | `memory/search.py` |
| search_memory() — FTS5 -> filter empty -> source weight -> truncate | Done | `memory/search.py` |
| _SOURCE_WEIGHTS (global:1.0, workspace:0.8, session:0.5) | Done | `memory/search.py` |
| MemoryStore — read/write global + workspace MEMORY.md | Done | `memory/file_store.py` |
| MemoryStore.append_session_log() — daily log with timestamp comment | Done | `memory/file_store.py` |
| _project_hash() — SHA256 of resolved cwd, first 12 hex chars | Done | `memory/file_store.py` |
| pyproject.toml — added aiosqlite>=0.20 dependency | Done | `pyproject.toml` |

**Progress: 18/18 items complete**

---

## Design Decisions

- **FTS5 contentless table (content='')**: Data lives in chunks table, FTS only stores index tokens. ~50% storage savings.
- **Non-invasive FTS5 detection**: Uses `PRAGMA compile_options` to check for `ENABLE_FTS5` — no table creation/destruction that could corrupt the database.
- **FTS5 delete via INSERT**: Contentless tables use `INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', ?, '')` instead of `DELETE FROM chunks_fts`.
- **blake3 optional**: chunker.py tries `import blake3` first, falls back to `hashlib.sha256` if unavailable. No hard dependency.
- **FTS5 operations wrapped in try/except**: Any FTS5 failure degrades gracefully (search falls to LIKE, index skip logs debug).
- **Session log format**: `{date}-{slug}-{sid_prefix}.md` with `<!-- flush HH:MM:SS UTC -->` timestamp comments.
