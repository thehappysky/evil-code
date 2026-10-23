# Module 2: Auth Layer

> Phase: 1 | Status: DONE | Created: 2026-07-19

---

## Scope

| Feature | Description |
|:---|:---|
| is_token_expired() | Sync check: ms timestamp + 5min safety buffer |
| refresh_token() | Async httpx POST, returns new dict (immutable pattern) |
| load_token() | Read ~/.claude/.credentials.json, return None on any failure |
| save_token() | Atomic write: mkstemp -> json.dump -> fsync -> os.replace -> chmod 600 |

---

## Final Architecture

```
code_agent/auth/
├── __init__.py          # exports: load_token, save_token, refresh_token, is_token_expired
├── token_refresh.py     # OAuth2 refresh (async httpx, pure function, no file I/O)
└── token_store.py       # Atomic file read/write (~/.claude/.credentials.json)
```

---

## Implementation Status

| Item | Status | File |
|:---|:---|:---|
| is_token_expired() - sync, buffer_seconds=300 | Done | `auth/token_refresh.py` |
| refresh_token() - async, optional client reuse | Done | `auth/token_refresh.py` |
| _TOKEN_URL / _CLIENT_ID constants | Done | `auth/token_refresh.py` |
| load_token() - graceful None on missing/invalid | Done | `auth/token_store.py` |
| save_token() - preserves other keys, atomic | Done | `auth/token_store.py` |

**Progress: 5/5 items complete**

---

## Design Decisions

- Pure function module: token_refresh.py does NOT read/write files (caller decides persistence)
- refresh_token() returns new dict, never mutates input
- Error classification: ValueError (no refreshToken) vs RuntimeError (HTTP/network)
- save_token() only replaces `claudeAiOauth` key, preserves mcpOAuth etc.
- chmod 600 wrapped in try/except (Windows may not support)
- Dependencies: httpx (already required by openai SDK) + stdlib only
