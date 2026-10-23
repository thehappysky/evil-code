# Module 3: Data Models

> Phase: 1 | Status: DONE | Created: 2026-07-19

---

## Scope

| Feature | Description |
|:---|:---|
| AgentError | Base exception for all Agent-specific errors |
| MaxTurnsReached | Carries max_turns context data |
| AuthenticationError | No token / invalid / refresh rejected |
| ToolNotFoundError | Carries tool_name for precise reporting |
| SystemMessage / UserMessage / AssistantMessage / ToolResultMessage | Tagged union via Literal role |
| ToolCallItem | Nested in AssistantMessage, stores raw JSON arguments |

---

## Final Architecture

```
code_agent/core/
├── exceptions.py    # AgentError -> MaxTurnsReached, AuthenticationError, ToolNotFoundError
└── messages.py      # Tagged union: System/User/Assistant/ToolResult + ToolCallItem
```

---

## Implementation Status

| Item | Status | File |
|:---|:---|:---|
| AgentError (base) | Done | `core/exceptions.py` |
| MaxTurnsReached (max_turns attr) | Done | `core/exceptions.py` |
| AuthenticationError | Done | `core/exceptions.py` |
| ToolNotFoundError (tool_name attr) | Done | `core/exceptions.py` |
| ToolCallItem (id, name, arguments as raw JSON) | Done | `core/messages.py` |
| SystemMessage (role="system") | Done | `core/messages.py` |
| UserMessage (role="user") | Done | `core/messages.py` |
| AssistantMessage (content + tool_calls) | Done | `core/messages.py` |
| ToolResultMessage (tool_call_id, is_error) | Done | `core/messages.py` |
| Message type alias (union) | Done | `core/messages.py` |

**Progress: 10/10 items complete**

---

## Design Decisions

- Flat exception hierarchy (one level only), each carries contextual data
- Pydantic BaseModel + Literal role achieves tagged union (replaces Rust enum)
- arguments stored as raw JSON string — parsing deferred to ToolRegistry.execute()
- is_error on ToolResultMessage lets model decide retry vs abandon
- Does NOT include Rust's BackendToolCall or Reasoning variants (Phase 1 scope)
