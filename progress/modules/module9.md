# Module 9: Unit Tests

> Phase: 1 | Status: DONE | Created: 2026-07-19

---

## Scope

| Feature | Description |
|:---|:---|
| conftest.py | Shared fixtures: MockChatClient, tmp dirs, sample tokens, event factories |
| test_auth/ | Token read/write/expiry/refresh (pytest-httpx for mock HTTP) |
| test_api/ | ChatClient auth flow (_ensure_client paths, close) |
| test_core/ | Agent loop with mock LLM, Session dangling repair, messages |
| test_tools/ | Registry register/finalize/execute, each built-in tool |

---

## Final Architecture

```
tests/
├── conftest.py            # Fixtures: MockChatClient, tmp_workspace, sample_token, event factories
├── test_auth/
│   ├── __init__.py
│   └── test_oauth.py      # is_token_expired, refresh_token, load/save_token (24 tests)
├── test_api/
│   ├── __init__.py
│   └── test_client.py     # _ensure_client auth paths, close() (7 tests)
├── test_core/
│   ├── __init__.py
│   ├── test_agent.py      # Agent.run() text/tool_call/max_turns/hooks (9 tests)
│   └── test_session.py    # Session add/to_api/repair (14 tests)
└── test_tools/
    ├── __init__.py
    ├── test_registry.py   # register/finalize/execute/truncate (12 tests)
    └── test_builtin.py    # file_read/file_edit/file_write/shell_exec/file_search (15 tests)
```

Total: 81 tests

---

## Implementation Status

| Item | Status | File |
|:---|:---|:---|
| conftest.py (MockChatClient, tmp dirs, sample token, factories) | Done | `tests/conftest.py` |
| test_auth: is_token_expired (expired/valid/buffer/no expiry) | Done | `tests/test_auth/test_oauth.py` |
| test_auth: refresh_token (success/no rotation/missing/HTTP error/invalid JSON) | Done | `tests/test_auth/test_oauth.py` |
| test_auth: load_token/save_token (missing/invalid/preserve keys/create dir) | Done | `tests/test_auth/test_oauth.py` |
| test_api: _ensure_client (no creds/valid/expired+refresh/failure/cached) | Done | `tests/test_api/test_client.py` |
| test_core: Agent.run() single-turn text response | Done | `tests/test_core/test_agent.py` |
| test_core: Agent.run() multi-turn tool call chain | Done | `tests/test_core/test_agent.py` |
| test_core: Agent.run() MaxTurnsReached | Done | `tests/test_core/test_agent.py` |
| test_core: Hook pipeline (DENY / exploding hook Layer 2 defense) | Done | `tests/test_core/test_agent.py` |
| test_core: Session dangling repair (4-step: paired/dangling/multi/dedup) | Done | `tests/test_core/test_session.py` |
| test_core: Session to_api_messages format (all message types) | Done | `tests/test_core/test_session.py` |
| test_tools: Registry register + finalize lock + definitions format | Done | `tests/test_tools/test_registry.py` |
| test_tools: Registry execute (valid/invalid/missing/exception/str normalize) | Done | `tests/test_tools/test_registry.py` |
| test_tools: Registry truncation (within/exceeds/error exempt) | Done | `tests/test_tools/test_registry.py` |
| test_tools: file_read (normal/binary/offset+limit/missing/empty) | Done | `tests/test_tools/test_builtin.py` |
| test_tools: file_edit (unique/multi/replace_all/not found/missing file) | Done | `tests/test_tools/test_builtin.py` |
| test_tools: file_write (create/overwrite/mkdir) | Done | `tests/test_tools/test_builtin.py` |
| test_tools: shell_exec (success/error exit/timeout/workdir) | Done | `tests/test_tools/test_builtin.py` |
| test_tools: file_search (match/no match/case insensitive/glob filter) | Done | `tests/test_tools/test_builtin.py` |

**Progress: 19/19 items complete**

---

## Design Decisions

- pytest + pytest-asyncio (asyncio_mode = "auto")
- pytest-httpx for mocking HTTP (token refresh)
- MockChatClient yields pre-defined StreamEvent sequences (no real LLM calls)
- tmp_path fixture for file-based tool tests
- web_fetch excluded from unit tests (DNS/redirect SSRF logic requires integration-level mocking)
- Circular import fix: core/__init__.py and api/__init__.py no longer re-export Agent/ChatClient (breaks api↔core cycle)
