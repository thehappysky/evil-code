# Module 4: LLM Communication

> Phase: 1 | Status: DONE | Created: 2026-07-19

---

## Scope

| Feature | Description |
|:---|:---|
| ToolDefinitionSchema | OpenAI function-calling format (sent TO model) |
| FunctionSchema / FunctionCall | Tool description / call details |
| ToolCall | Model's tool call request (id + type + function) |
| ToolCallDelta | Streaming fragment (index + id + name + arguments_fragment) |
| StreamEvent | 5 kinds: text_delta, tool_call_delta, thinking_delta, turn_end, error |
| ChatClient | Lazy init + _ensure_client() auto-refresh + chat_stream() async generator |

---

## Final Architecture

```
code_agent/api/
├── __init__.py      # exports: ChatClient, StreamEvent, ToolCall
├── schemas.py       # Pydantic models for API communication
└── client.py        # AsyncOpenAI wrapper with auth management
```

---

## Implementation Status

| Item | Status | File |
|:---|:---|:---|
| FunctionSchema (name, description, parameters) | Done | `api/schemas.py` |
| ToolDefinitionSchema (type="function") | Done | `api/schemas.py` |
| FunctionCall (name, arguments as raw JSON) | Done | `api/schemas.py` |
| ToolCall (id, type, function) | Done | `api/schemas.py` |
| ToolCallDelta (index, id, name, arguments_fragment) | Done | `api/schemas.py` |
| StreamEvent (5 kinds + usage on turn_end) | Done | `api/schemas.py` |
| ChatClient.__init__ (model, base_url, no token read) | Done | `api/client.py` |
| ChatClient._ensure_client() (load->check->refresh->create) | Done | `api/client.py` |
| ChatClient.chat_stream() (async generator, SSE parsing) | Done | `api/client.py` |
| ChatClient.close() (cleanup httpx resources) | Done | `api/client.py` |
| Auth failure -> AuthenticationError (no silent degradation) | Done | `api/client.py` |

**Progress: 11/11 items complete**

---

## Design Decisions

- Lazy initialization: don't read token in __init__ (TUI may start before auth)
- _ensure_client() raises AuthenticationError on any auth failure — no degradation
- chat_stream() yields StreamEvent; Agent accumulates tool_call_delta by index
- thinking_delta: defined in Phase 1, produced when model returns reasoning tokens
- Separate httpx.AsyncClient for token refresh (openai SDK doesn't expose its client)
- 401/unauthorized in API response -> reset client + raise AuthenticationError
