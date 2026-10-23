-- Memory index schema for SQLite + FTS5
-- References: Rust MemoryIndex DDL

CREATE TABLE IF NOT EXISTS chunks (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,          -- "{path}:{chunk_index}"
    path TEXT NOT NULL,               -- source file path
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    text TEXT NOT NULL,               -- chunk content
    hash TEXT NOT NULL,               -- blake3 hex digest
    source TEXT NOT NULL,             -- "global" | "workspace" | "session"
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    access_count INTEGER DEFAULT 0,
    last_accessed INTEGER
);

CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(hash);

-- FTS5 contentless table: data lives in chunks, FTS only stores index tokens
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='');
