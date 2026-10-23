# Phase 1: Minimal Viable Agent - Progress

> Goal: Complete "user input -> LLM reasoning -> tool call -> result" loop in CLI
> Estimated: 3-5 days
> Status: DONE (10/10 modules + Multi-Provider Architecture Enhancement)

---

## Scope: What Phase 1 Must Deliver

1. **Auth**: OAuth token read/refresh + evil-code native config.yaml + settings.json reader (cc-switch integration)
2. **LLM Client**: httpx direct + Backend Protocol (multi-provider: OpenAI/Anthropic/DeepSeek) + streaming SSE
3. **Agent Loop**: think -> act -> observe cycle with AgentHook middleware
4. **Session**: Conversation history management + dangling call repair + token estimation
5. **Tool Registry**: Decorator registration + finalize lock + Pydantic validation + dispatch
6. **6 Built-in Tools**: file_read, file_search, shell_exec, file_edit, file_write, web_fetch
7. **CLI**: argparse entry with headless (single prompt) + REPL (interactive) modes + `config set/show/clear` subcommand + 5-level auth resolution
8. **Tests**: pytest coverage for auth, agent loop, tools, session, SSE, backends, settings
9. **Multi-Provider Enhancement**: api/sse.py + api/backends/ (3 wire formats) + auth/settings_store.py + ProviderConfig + ApiBackend/AuthScheme enums

---

## Final Architecture (Phase 1)

```
code_agent/
├── __init__.py                    # version
├── auth/
│   ├── __init__.py                # exports: load_token, save_token, refresh_token, is_token_expired, load_settings_credentials, load_config, save_config, generate_config
│   ├── token_refresh.py           # OAuth2 refresh (async httpx, pure function)
│   ├── token_store.py             # Atomic file read/write (~/.claude/.credentials.json)
│   ├── settings_store.py          # Read ~/.claude/settings.json env block (cc-switch integration) [NEW]
│   └── config_store.py            # Native config read/write/generate (~/.evil-code/config.yaml) [NEW]
├── config/
│   ├── __init__.py                # exports: generate_config, load_config, save_config, save_settings_config, show_settings_config, clear_settings_config
│   └── settings_writer.py         # Write ~/.claude/settings.json env block (cc-switch compatible) [NEW]
├── api/
│   ├── __init__.py                # exports: StreamEvent, ToolCall, ApiBackend, AuthScheme, ProviderConfig
│   ├── client.py                  # ChatClient (httpx direct + Backend Protocol dispatch, no openai SDK) [REWRITTEN]
│   ├── schemas.py                 # Pydantic models + ApiBackend/AuthScheme enums + ProviderConfig dataclass
│   ├── sse.py                     # Shared SSE stream parser: iter_sse_events() [NEW]
│   └── backends/                  # Three wire-format backends [NEW]
│       ├── __init__.py            # Factory: get_backend(ApiBackend) -> Backend
│       ├── base.py                 # Backend Protocol + BackendRequest dataclass
│       ├── chat_completions.py    # OpenAI /chat/completions backend
│       ├── messages.py            # Anthropic /messages backend (DeepSeek /anthropic compatible)
│       └── responses.py           # OpenAI /responses backend
├── core/
│   ├── __init__.py                # exports: Session, exceptions (Agent not re-exported: circular import)
│   ├── agent.py                   # Main loop + AgentHook protocol + HookDecision
│   ├── session.py                 # Conversation state + dangling repair + token count
│   ├── messages.py                # Tagged union: System/User/Assistant/ToolResult
│   └── exceptions.py             # AgentError, MaxTurnsReached, AuthenticationError, ToolNotFoundError
├── tools/
│   ├── __init__.py                # exports: ToolRegistry, ToolDefinition, ToolResult
│   ├── registry.py                # Register + finalize + execute + truncate
│   ├── definition.py              # ToolDefinition (name + description + input_model + handler)
│   ├── result.py                  # ToolResult (output + is_error + truncated)
│   └── builtin/
│       ├── file_read.py           # Read file (binary detect + line numbers + truncation)
│       ├── file_search.py         # Code search (rg preferred, Python re fallback)
│       ├── shell_exec.py          # Shell exec (shlex + subprocess + timeout kill)
│       ├── file_edit.py           # search_replace (unique match + atomic write + diff)
│       ├── file_write.py          # write_file (create/overwrite + safety check + atomic)
│       └── web_fetch.py           # URL fetch (SSRF protection + HTML->Markdown)
├── cli/
│   └── main.py                    # argparse + _build_tools + _build_agent + 5-level auth + config set/show/clear + headless/REPL [REWRITTEN]
└── helpers/
    ├── token_counter.py           # bytes/4 estimation + threshold check
    ├── text_truncate.py           # truncate_with_preview + truncate_middle
    ├── file_io.py                 # atomic_write (shared by file_edit + file_write)
    └── tracer.py                  # TraceWriter (JSONL 审计日志，chat_stream 的 trace 参数)
```

---

## Module Progress

### Module 1: Project Skeleton + Helpers [DONE]

| Item | Status | Location |
|:---|:---|:---|
| pyproject.toml (hatchling, deps, entry point) | Done | `pyproject.toml` |
| Package structure (6 sub-packages + tests/) | Done | `code_agent/` |
| estimate_tokens() - bytes/4 heuristic | Done | `helpers/token_counter.py` |
| exceeds_threshold() - percentage check | Done | `helpers/token_counter.py` |
| truncate_with_preview() - head + footer | Done | `helpers/text_truncate.py` |
| truncate_middle() - head + tail + ellipsis | Done | `helpers/text_truncate.py` |
| atomic_write() - temp + fsync + os.replace | Done | `helpers/file_io.py` |
| TraceWriter - JSONL 审计日志 (api/client.py 使用) | Done | `helpers/tracer.py` |

### Module 2: Auth Layer [DONE]

| Item | Status | Location |
|:---|:---|:---|
| is_token_expired() - sync, ms timestamp + buffer | Done | `auth/token_refresh.py` |
| refresh_token() - async httpx POST, immutable | Done | `auth/token_refresh.py` |
| load_token() - read ~/.claude/.credentials.json | Done | `auth/token_store.py` |
| save_token() - atomic write (mkstemp+fsync+replace) | Done | `auth/token_store.py` |
| load_settings_credentials() - read ~/.claude/settings.json env block (cc-switch) | Done | `auth/settings_store.py` |
| load_config() - read ~/.evil-code/config.yaml (native config, auto-login) | Done | `auth/config_store.py` |
| save_config() - atomic write ~/.evil-code/config.yaml | Done | `auth/config_store.py` |
| generate_config() - accept params, produce EvilCodeConfig ready to save | Done | `auth/config_store.py` |

### Module 3: Data Models [DONE]

| Item | Status | Location |
|:---|:---|:---|
| AgentError / MaxTurnsReached / AuthenticationError / ToolNotFoundError | Done | `core/exceptions.py` |
| SystemMessage / UserMessage / AssistantMessage / ToolResultMessage | Done | `core/messages.py` |
| Message type alias (union of all 4 message types) | Done | `core/messages.py` |
| ToolCallItem (nested in AssistantMessage) | Done | `core/messages.py` |

### Module 4: LLM Communication [DONE]

| Item | Status | Location |
|:---|:---|:---|
| StreamEvent / ToolCall / FunctionCall / ToolDefinitionSchema / FunctionSchema | Done | `api/schemas.py` |
| ToolCallDelta (streaming accumulation model) | Done | `api/schemas.py` |
| ApiBackend enum (chat_completions / messages / responses) | Done | `api/schemas.py` |
| AuthScheme enum (bearer / x_api_key) | Done | `api/schemas.py` |
| ProviderConfig dataclass (api_backend, base_url, api_key, auth_scheme, model, max_tokens, temperature) | Done | `api/schemas.py` |
| ChatClient (httpx direct, backend dispatch, chat_stream + trace 审计日志, per-request override) | Done | `api/client.py` |
| iter_sse_events (shared SSE parser: data/event/DONE/multiline) | Done | `api/sse.py` |
| Backend Protocol + BackendRequest (url, headers, body) | Done | `api/backends/base.py` |
| ChatCompletions backend (build_request + parse_stream) | Done | `api/backends/chat_completions.py` |
| Messages backend (message conversion + tool conversion + parse_stream) | Done | `api/backends/messages.py` |
| Responses backend (input items + store=false + parse_stream) | Done | `api/backends/responses.py` |
| get_backend factory (singleton dispatch) | Done | `api/backends/__init__.py` |

### Module 5: Tool Infrastructure [DONE]

| Item | Status | Location |
|:---|:---|:---|
| ToolResult (ok/error class methods) | Done | `tools/result.py` |
| ToolDefinition (to_openai_schema) | Done | `tools/definition.py` |
| ToolRegistry (register/finalize/execute/get_definitions/list_names/_maybe_truncate) | Done | `tools/registry.py` |

### Module 6: Built-in Tools [DONE]

| Item | Status | Location |
|:---|:---|:---|
| file_read (binary detect + line numbers + offset/limit) | Done | `tools/builtin/file_read.py` |
| file_search (rg + Python re fallback) | Done | `tools/builtin/file_search.py` |
| shell_exec (shlex + asyncio subprocess + timeout) | Done | `tools/builtin/shell_exec.py` |
| file_edit / search_replace (unique match + atomic + diff) | Done | `tools/builtin/file_edit.py` |
| file_write (safety check + mkdir + atomic + diff) | Done | `tools/builtin/file_write.py` |
| web_fetch (SSRF 3-layer + HTML->Markdown + redirect re-check) | Done | `tools/builtin/web_fetch.py` |

### Module 7: Agent Core [DONE]

| Item | Status | Location |
|:---|:---|:---|
| Session (add_*/to_api_messages/get_system_prompt/_repair_dangling_calls) | Done | `core/session.py` |
| Session properties (messages/prompt_count/estimated_tokens) | Done | `core/session.py` |
| AgentHook protocol + HookDecision enum | Done | `core/agent.py` |
| Agent.run() - async generator with hook call points | Done | `core/agent.py` |
| Agent._execute_one_tool() - hook chain + defensive exception handling | Done | `core/agent.py` |

### Module 8: CLI Entry [DONE]

| Item | Status | Location |
|:---|:---|:---|
| _build_tools() - register 6 tools + finalize | Done | `cli/main.py` |
| _build_agent() - hook assembly (open-closed principle) | Done | `cli/main.py` |
| _run_headless() / _run_interactive(agent, args, client, tools) | Done | `cli/main.py` |
| main() - argparse entry (prompt, --cwd, --model, --max-turns, --base-url, --api-key, --provider) | Done | `cli/main.py` |
| _resolve_provider_config() - 5-level auth resolution (CLI > config.yaml > env > settings.json > OAuth) | Done | `cli/main.py` |
| _infer_backend() - auto-detect wire format from base_url | Done | `cli/main.py` |
| _infer_auth_scheme() - Messages + anthropic.com -> XApiKey, else Bearer | Done | `cli/main.py` |
| _resolve_oauth_config() - OAuth fallback (load_token -> is_token_expired -> refresh -> save) | Done | `cli/main.py` |
| _run_config_command() - config set/show/clear subcommand (parameter -> config file) | Done | `cli/main.py` |
| config set: writes to ~/.evil-code/config.yaml (primary) + ~/.claude/settings.json (fallback) | Done | `cli/main.py` |

### Module 9: Tests [DONE]

| Item | Status | Location |
|:---|:---|:---|
| test_auth (token read/refresh/expiry) | Done | `tests/test_auth/` |
| test_api (ChatClient httpx direct: auth, stream, close) | Done | `tests/test_api/` |
| test_api (SSE parser: data/event/DONE/multiline/comment) | Done | `tests/test_api/test_sse.py` |
| test_api (settings_store: cc-switch settings.json reader) | Done | `tests/test_auth/test_settings_store.py` |
| test_api (backends: 22 tests across 3 backends) | Done | `tests/test_api/test_backends.py` |
| test_api (auth resolution: backend inference + 5-level priority chain, 25 tests) | Done | `tests/test_api/test_auth_resolution.py` |
| test_core (agent loop with mock LLM, session) | Done | `tests/test_core/` |
| test_tools (registry, built-in tools) | Done | `tests/test_tools/` |
| conftest.py (fixtures: MockChatClient, tmp dirs, factories) | Done | `tests/conftest.py` |

---

## Verification Criteria (from design doc)

**P0 (must have)**:
- [x] Single-turn "Read this file" request works (model calls file_read)
- [x] Multi-turn "Find and fix this bug" works (model calls grep + read + bash)

**P1 (significant UX improvement)**:
- [x] Token auto-refresh on expiry
- [x] Multi-provider SSE parsing (iter_sse_events)
- [x] settings.json auth (cc-switch integration)
- [x] Three-backend request building + stream parsing
- [x] ChatClient httpx direct (401 → AuthError)
- [ ] Ctrl+C gracefully cancels current turn

---

## Module 10: Multi-Provider Architecture Enhancement [DONE]

> Architecture补强 — not a new Phase, but a Phase 1 structural enhancement
> to support any LLM provider (OpenAI, Anthropic, DeepSeek, xAI, Ollama, etc.)

### Motivation

The original Phase 1 code hardcoded the openai SDK + Claude OAuth path. The design doc's LLMClient Protocol was intended but never implemented. Users with DeepSeek (via cc-switch settings.json) or other providers could not use the agent at all — the code would fail with "No credentials found" on machines without ~/.claude/.credentials.json.

### What Changed

| Area | Before | After |
|:---|:---|:---|
| HTTP client | AsyncOpenAI SDK | httpx.AsyncClient direct |
| Provider support | Claude OAuth only | Any provider via 3 wire-format backends |
| Auth | OAuth token only | 5-level: CLI --api-key > config.yaml > env > settings.json > OAuth |
| Config | model + base_url args | ProviderConfig dataclass (backend + auth + model + temp) |
| SSE parsing | openai SDK internal | iter_sse_events() shared parser |
| Wire formats | OpenAI Chat Completions only | 3 backends: Chat Completions, Messages, Responses |

### New Files

| File | Purpose |
|:---|:---|
| `api/sse.py` | Shared SSE stream parser (iter_sse_events) |
| `api/backends/base.py` | Backend Protocol + BackendRequest dataclass |
| `api/backends/chat_completions.py` | OpenAI /chat/completions backend |
| `api/backends/messages.py` | Anthropic /messages backend (DeepSeek compatible) |
| `api/backends/responses.py` | OpenAI /responses backend |
| `api/backends/__init__.py` | Factory: get_backend(ApiBackend) -> Backend |
| `auth/settings_store.py` | Read ~/.claude/settings.json env block (cc-switch) |
| `auth/config_store.py` | Native config read/write/generate (~/.evil-code/config.yaml) |
| `config/settings_writer.py` | Write ~/.claude/settings.json env block (cc-switch compatible) |
| `config/__init__.py` | Config package exports (generate/save/load + settings writer) |

### Modified Files

| File | Changes |
|:---|:---|
| `api/schemas.py` | Added ApiBackend, AuthScheme enums + ProviderConfig dataclass |
| `api/client.py` | Complete rewrite: httpx direct + backend dispatch + per-request override |
| `api/__init__.py` | Added ApiBackend, AuthScheme, ProviderConfig to exports |
| `auth/__init__.py` | Added load_settings_credentials, load_config, save_config, generate_config to exports |
| `cli/main.py` | Complete rewrite: 5-level auth resolution + --api-key/--provider args + config set/show/clear subcommand |
| `tests/conftest.py` | Added factories + evil-code config file fixtures |
| `pyproject.toml` | Removed openai dependency |

### New Tests (76 total, includes auth resolution + config store)

| File | Tests | Coverage |
|:---|:---|:---|
| `tests/test_api/test_sse.py` | 6 | SSE parsing: data, DONE, event, multiline, comment, invalid JSON |
| `tests/test_auth/test_settings_store.py` | 7 | settings.json: not found, invalid, AUTH_TOKEN, API_KEY, priority, neither, extra keys |
| `tests/test_api/test_backends.py` | 22 | ChatCompletions (7), Messages (8), Responses (7) |
| `tests/test_api/test_client.py` | 6 (rewritten) | ChatClient: missing key, 401, non-200, stream, override, close |
| `tests/test_api/test_auth_resolution.py` | 25 | Auth resolution: backend inference + 5-level priority chain |
| `tests/test_auth/test_config_store.py` | 17 | Config store: read/write/generate/roundtrip/tempfile + clear + conflict |

### Design Decisions

- httpx replaces openai SDK: no SDK lock-in, full control over wire format
- Backend Protocol (structural typing): each backend implements build_request + parse_stream
- Shared SSE parser: backends only interpret JSON content, not raw bytes
- 5-level auth resolution: CLI --api-key > config.yaml > env > settings.json > OAuth
- evil-code native config: ~/.evil-code/config.yaml — auto-login, no CLI args needed
- cc-switch integration: reading settings.json env block = zero-config for cc-switch users
- Auto backend detection: base_url containing "anthropic" or "deepseek" → Messages backend
- DeepSeek /anthropic endpoint speaks Anthropic Messages API, NOT OpenAI Chat Completions

---

## Module 11: Config Tool + TUI [DONE]

> Created: 2026-07-25 | See: progress/modules/module11.md

### Motivation

User feedback: "正常使用应该有一段代码接受各种参数，然后生成配置文件" and "我想要直接测试 agent，配置 settings 免登录，然后直接能使用简易的 UI 去操作，可以问问题，有对话框". The test plan was too simplistic — manual JSON editing is not a proper workflow. Need: configure tool + interactive UI.

### What Changed

- **config/settings_writer.py**: Parameter to settings.json writer (cc-switch equivalent). save/show/clear with atomic write, merge preserves non-env keys, ConfigSummary NamedTuple.
- **tui/app.py**: Textual TUI — EvilCodeApp with header bar (model+backend), RichLog streaming chat, Input bar, tool call markers, error display.
- **cli/main.py**: _run_config_command (config set/show/clear), --tui flag routing.
- **Unicode fix**: ✓/✗/— → ASCII in config output (Windows GBK console).

### New Files

| File | Purpose |
|:---|:---|
| `config/__init__.py` | Package exports |
| `config/settings_writer.py` | save/show/clear settings.json (cc-switch equivalent) |
| `tui/__init__.py` | Package exports |
| `tui/app.py` | EvilCodeApp Textual TUI |
| `tests/test_config/__init__.py` | Test package |
| `tests/test_config/test_settings_writer.py` | 17 tests |

### New Tests (17 total)

| Test File | Tests | Key Cases |
|:---|:---|:---|
| `tests/test_config/test_settings_writer.py` | 17 | save (5), show (4), clear (4), _mask_key (4) |

Total tests: 140 → 157 (all passing).

---

## Dependencies (Phase 1)

```
pip install httpx pydantic markdownify  # openai SDK removed
pip install -e ".[tui]"  # textual for TUI mode
# dev:
pip install pytest pytest-asyncio pytest-httpx
```
