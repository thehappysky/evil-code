# Module 10: Multi-Provider Architecture Enhancement

> Phase: 1+ | Status: DONE | Created: 2026-07-24

---

## Scope

| Feature | Description |
|:---|:---|
| api/sse.py | Shared SSE stream parser: iter_sse_events() — data lines, event markers, [DONE] terminator, multi-line data, comment skipping |
| api/backends/base.py | Backend Protocol (structural typing) + BackendRequest dataclass (url, headers, body) |
| api/backends/chat_completions.py | OpenAI /chat/completions backend: build_request (messages pass-through, Bearer/XApiKey auth) + parse_stream (delta.content, delta.tool_calls, finish_reason) |
| api/backends/messages.py | Anthropic /messages backend: _convert_messages (system extraction, tool→user tool_result, tool_calls→tool_use) + _convert_tools (function→input_schema) + parse_stream (content_block_start/delta, message_delta) |
| api/backends/responses.py | OpenAI /responses backend: _convert_messages (input items) + store=false (ZDR) + max_output_tokens + parse_stream (output_text.delta, function_call_arguments.delta) |
| api/backends/__init__.py | Factory: get_backend(ApiBackend) -> Backend (singleton dispatch) |
| auth/settings_store.py | Read ~/.claude/settings.json env block: ANTHROPIC_AUTH_TOKEN (Bearer) / ANTHROPIC_API_KEY (XApiKey) + BASE_URL + MODEL |
| api/schemas.py (modified) | Added ApiBackend, AuthScheme enums + ProviderConfig dataclass |
| api/client.py (rewritten) | httpx.AsyncClient direct, backend dispatch via get_backend(), per-request override via dataclasses.replace() |
| cli/main.py (rewritten) | 5-level auth resolution: --api-key > config.yaml > env > settings.json > OAuth; config set/show/clear subcommand; --provider arg |
| pyproject.toml (modified) | Removed openai>=1.30.0 dependency |

---

## Final Architecture

```
code_agent/api/
├── __init__.py                # exports: StreamEvent, ToolCall, ApiBackend, AuthScheme, ProviderConfig
├── client.py                  # ChatClient: httpx direct, no openai SDK
├── schemas.py                 # + ApiBackend enum, AuthScheme enum, ProviderConfig dataclass
├── sse.py                     # iter_sse_events(response) -> AsyncIterator[dict]
└── backends/
    ├── __init__.py            # get_backend(ApiBackend) -> Backend (singleton)
    ├── base.py                # Backend Protocol + BackendRequest(url, headers, body)
    ├── chat_completions.py    # /chat/completions — OpenAI format
    ├── messages.py            # /messages — Anthropic format (DeepSeek compatible)
    └── responses.py           # /responses — OpenAI Responses API

code_agent/auth/
├── __init__.py                # + load_settings_credentials export
├── token_refresh.py           # unchanged
├── token_store.py             # unchanged
└── settings_store.py          # load_settings_credentials() -> SettingsCredentials
```

---

## Implementation Status

| Item | Status | File |
|:---|:---|:---|
| iter_sse_events: data line parsing + JSON decode | Done | `api/sse.py` |
| iter_sse_events: [DONE] terminator | Done | `api/sse.py` |
| iter_sse_events: event: type marker → _event key | Done | `api/sse.py` |
| iter_sse_events: multi-line data joining | Done | `api/sse.py` |
| iter_sse_events: comment (:) skipping | Done | `api/sse.py` |
| iter_sse_events: invalid JSON skip (no crash) | Done | `api/sse.py` |
| Backend Protocol: build_request + parse_stream | Done | `api/backends/base.py` |
| BackendRequest dataclass (url, headers, body) | Done | `api/backends/base.py` |
| ChatCompletions: build_request (url, Bearer/XApiKey headers) | Done | `api/backends/chat_completions.py` |
| ChatCompletions: build_request (messages, tools, stream_options) | Done | `api/backends/chat_completions.py` |
| ChatCompletions: parse_stream (text_delta, tool_call_delta, turn_end) | Done | `api/backends/chat_completions.py` |
| Messages: _convert_messages (system extraction, tool→user tool_result) | Done | `api/backends/messages.py` |
| Messages: _convert_messages (tool_calls → tool_use blocks, arguments JSON→input) | Done | `api/backends/messages.py` |
| Messages: _flush_tool_results (list copy before clear — bug fix) | Done | `api/backends/messages.py` |
| Messages: _convert_tools (function → input_schema) | Done | `api/backends/messages.py` |
| Messages: parse_stream (content_block_start, input_json_delta, message_delta) | Done | `api/backends/messages.py` |
| Messages: block_to_tool mapping (block index ≠ tool index) | Done | `api/backends/messages.py` |
| Responses: _convert_messages (input items format) | Done | `api/backends/responses.py` |
| Responses: store=false (ZDR compliance) | Done | `api/backends/responses.py` |
| Responses: max_output_tokens (not max_tokens) | Done | `api/backends/responses.py` |
| Responses: parse_stream (output_text.delta, function_call_arguments.delta) | Done | `api/backends/responses.py` |
| Responses: output_index → tool_index mapping | Done | `api/backends/responses.py` |
| get_backend factory (singleton dict) | Done | `api/backends/__init__.py` |
| load_settings_credentials (AUTH_TOKEN Bearer priority) | Done | `auth/settings_store.py` |
| load_settings_credentials (API_KEY XApiKey fallback) | Done | `auth/settings_store.py` |
| load_settings_credentials (file not found → is_present=False) | Done | `auth/settings_store.py` |
| ProviderConfig dataclass (7 fields with defaults) | Done | `api/schemas.py` |
| ApiBackend enum (3 values) | Done | `api/schemas.py` |
| AuthScheme enum (2 values) | Done | `api/schemas.py` |
| ChatClient.__init__ (config: ProviderConfig) | Done | `api/client.py` |
| ChatClient.chat_stream (backend.build_request → httpx.stream → iter_sse → backend.parse_stream) | Done | `api/client.py` |
| ChatClient.chat_stream (per-request override via dataclasses.replace) | Done | `api/client.py` |
| ChatClient: HTTP 401 → AuthenticationError | Done | `api/client.py` |
| ChatClient: non-200 → error StreamEvent | Done | `api/client.py` |
| ChatClient.close (http_client.aclose) | Done | `api/client.py` |
| cli: _resolve_provider_config (5-level chain) | Done | `cli/main.py` |
| cli: _infer_backend (auto: anthropic/deepseek → Messages, else ChatCompletions) | Done | `cli/main.py` |
| cli: _infer_auth_scheme (Messages + api.anthropic.com → XApiKey) | Done | `cli/main.py` |
| cli: _resolve_oauth_config (async: load → expire check → refresh → save) | Done | `cli/main.py` |
| cli: --api-key arg (highest priority auth) | Done | `cli/main.py` |
| cli: --provider arg (auto/chat_completions/messages/responses) | Done | `cli/main.py` |
| pyproject.toml: removed openai dependency | Done | `pyproject.toml` |

**Progress: 38/38 items complete**

---

## Design Decisions

- **httpx over openai SDK**: The openai SDK locks us to OpenAI's wire format. With httpx direct + Backend Protocol, each provider's format is handled by a dedicated backend module. Adding a new provider = adding a new backend file, not modifying core code.
- **Structural typing (Protocol)**: Python's `Protocol` from `typing` provides structural subtyping — backends don't need to inherit from a base class, they just need to implement the right methods. This is lighter than ABC and more flexible.
- **Shared SSE parser**: `iter_sse_events()` handles the SSE wire format (data lines, event markers, [DONE]) once. Backends only interpret the JSON payload — they don't parse raw bytes. This eliminates duplicated SSE parsing logic.
- **5-level auth resolution**: CLI --api-key (level 1) for explicit override. config.yaml (level 2) for evil-code native auto-login. Environment variables (level 3) for CI/CD. settings.json (level 4) for cc-switch desktop users. OAuth (level 5) for Claude OAuth users.
- **cc-switch integration**: cc-switch Tauri writes ~/.claude/settings.json with ANTHROPIC_AUTH_TOKEN/API_KEY + BASE_URL + MODEL. evil-code reads the same file via `load_settings_credentials()` — zero-config integration, no additional setup needed.
- **Auto backend detection**: When --provider is "auto" (default), the CLI inspects base_url. URLs containing "anthropic" or "deepseek" (with /anthropic path) → Messages backend. Everything else → ChatCompletions. This handles the DeepSeek /anthropic endpoint correctly — it speaks Anthropic Messages API, not OpenAI Chat Completions.
- **Messages backend complexity**: The Anthropic Messages API has a fundamentally different message format from OpenAI. system messages become a top-level field. tool results become user messages with tool_result content blocks. tool_calls in assistant messages become tool_use content blocks with arguments as JSON strings (streamed as input_json_delta fragments). The block index in streaming ≠ tool index (text blocks and tool_use blocks are interleaved), requiring a block_to_tool mapping.
- **Responses backend store=false**: The Responses API has a `store` parameter. Setting it to false ensures ZDR (Zero Data Retention) compliance — the server doesn't persist conversation data.
- **list(pending) bug fix**: In the Messages backend's `_flush_tool_results()`, appending `pending` directly as content and then calling `pending.clear()` would empty the same reference. Fixed by copying with `list(pending)` before clearing.
- **_infer_backend_from_url bug fix**: The original `_infer_backend(args)` used `args.base_url` (argparse default: `https://api.anthropic.com/v1`) for ALL auth levels. For levels 2 (env vars) and 3 (settings.json), the actual base_url comes from env/settings, not argparse. This meant a settings.json with `ANTHROPIC_BASE_URL=https://api.openai.com/v1` would still be inferred as Messages (because the default URL contains "anthropic"). Fixed by extracting `_infer_backend_from_url(base_url, provider)` as a pure function and re-calling it at levels 2 and 3 with the actual resolved base_url. Same pattern applied to `_infer_auth_scheme_from_url()`.

---

## Test Coverage

| Test File | Tests | Key Cases |
|:---|:---|:---|
| `tests/test_api/test_sse.py` | 6 | simple data, [DONE], event marker, multi-line, comment skip, invalid JSON skip |
| `tests/test_auth/test_settings_store.py` | 7 | not found, invalid JSON, AUTH_TOKEN Bearer, API_KEY XApiKey, both (priority), neither, extra keys |
| `tests/test_api/test_backends.py` | 22 | ChatCompletions build (4) + parse (3), Messages build (6) + parse (2), Responses build (5) + parse (2) |
| `tests/test_api/test_client.py` | 6 | missing key, 401→AuthError, non-200→error event, successful stream, per-request override, close cleanup |
| `tests/test_api/test_auth_resolution.py` | 25 | backend inference (8), auth scheme (4), level 1 CLI (4), level 2 env (3), level 3 settings (3), priority chain (3) |
| **Total new tests** | **66** | (34 net new + 7 rewritten client + 25 auth resolution) |

All 140 tests pass (81 original - 7 old client + 6 new client + 6 SSE + 7 settings + 22 backends + 25 auth resolution = 140).
