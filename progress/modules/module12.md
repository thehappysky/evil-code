# Phase 2 Module 1: storage/ — Session JSONL Persistence

> Phase: 2 | Status: DONE | Created: 2026-07-25

---

## Scope

| Feature | Description |
|:---|:---|
| SessionStore | JSONL session persistence: init, append, load, delta, list, find_latest |
| DeltaIterator | Line-by-line JSONL reader with byte offset tracking |
| FileLock | Cross-platform file lock: msvcrt.locking (Windows) / fcntl.flock (POSIX) |
| SessionMeta | Pydantic model: session_id, cwd, model_id, title, timestamps, counts |

---

## Final Architecture

```
code_agent/storage/
├── __init__.py          # exports: SessionStore, DeltaIterator, SessionMeta, FileLock
├── session_store.py     # JSONL read/write + append + delta replay (289 lines)
├── file_lock.py         # Cross-platform file lock: sync + async context manager (120 lines)
└── models.py            # SessionMeta Pydantic model (33 lines)
```

---

## Implementation Status

| Item | Status | File |
|:---|:---|:---|
| SessionMeta model (session_id, cwd, model_id, title, timestamps, counts) | Done | `storage/models.py` |
| FileLock (Windows msvcrt / POSIX fcntl, sync + async CM) | Done | `storage/file_lock.py` |
| _encode_cwd() — URL-safe base64 encode | Done | `storage/session_store.py` |
| _generate_session_id() — timestamp + UUID prefix | Done | `storage/session_store.py` |
| SessionStore.init_session() — create dir + initial summary.json | Done | `storage/session_store.py` |
| SessionStore.append_message() — JSONL append + torn-tail repair | Done | `storage/session_store.py` |
| SessionStore.update_meta() — atomic summary.json update with FileLock | Done | `storage/session_store.py` |
| SessionStore.save_full_history() — atomic overwrite (post-compaction) | Done | `storage/session_store.py` |
| SessionStore.load_session() — read summary.json + chat_history.jsonl | Done | `storage/session_store.py` |
| SessionStore.load_delta() — read messages from offset | Done | `storage/session_store.py` |
| SessionStore.list_sessions() — all sessions sorted by updated_at desc | Done | `storage/session_store.py` |
| SessionStore.find_latest(cwd) — most recent session for cwd | Done | `storage/session_store.py` |
| SessionStore.get_history_byte_offset() — file size for delta checkpoint | Done | `storage/session_store.py` |
| DeltaIterator — line-by-line JSONL with offset tracking | Done | `storage/session_store.py` |
| pyproject.toml — added aiofiles>=23.0 dependency | Done | `pyproject.toml` |

**Progress: 15/15 items complete**

---

## Design Decisions

- **Why not SQLite for sessions?** Session data is a linear append-only stream. JSONL is simpler to debug (tail -f), backup (cp), and migrate.
- **Append-first:** O(1) writes with no transactions needed. Equivalent to Rust JSONL strategy.
- **Torn-tail repair:** On append, checks last byte is newline before writing. On read, silently drops invalid last line.
- **Atomic writes:** summary.json uses temp file + os.fsync + os.replace pattern. chat_history.jsonl uses append + fsync.
- **File lock timeout:** 30s default, configurable. Prevents indefinite blocking in edge cases.
- **aiofiles** for async file I/O — non-blocking event loop during session I/O.

### Design Doc Discrepancy

| Item | Design Doc | Implementation | Reason |
|:---|:---|:---|:---|
| DeltaIterator.__anext__ | Uses BufReader + seek | Uses aiofiles (async open + readline) | Better async integration; avoids buffering issues |
| append_message signature | `(self, session_id, message)` | `(self, session_id, cwd, message)` | cwd needed to locate session directory; consistent with other methods |
| update_meta signature | `(self, session_id, patch)` | `(self, session_id, cwd, patch)` | Same reason — cwd required for directory resolution |
