# Module 7: Agent Core

> Phase: 1 | Status: DONE | Created: 2026-07-19

---

## Scope

| Feature | Description |
|:---|:---|
| Session | Conversation state: add messages, to_api_messages, dangling repair, token estimation |
| _repair_dangling_calls() | Inject placeholder ToolResult for unpaired tool calls |
| AgentHook protocol | 5 lifecycle methods (on_session_start/turn_start/tool_call/tool_result/turn_end) |
| HookDecision enum | ALLOW / DENY / MODIFY |
| Agent.run() | Async generator, stream tool_call accumulation by index, hook call points |
| Agent._execute_one_tool() | JSON parse -> hook chain -> execute -> on_tool_result, two-layer exception defense |

---

## Final Architecture

```
code_agent/core/
├── session.py     # Session class (conversation state management)
└── agent.py       # AgentHook protocol + HookDecision + Agent class
```

---

## Implementation Status

| Item | Status | File |
|:---|:---|:---|
| Session.__init__ (system_prompt optional) | Done | `core/session.py` |
| Session.add_user_message (triggers repair) | Done | `core/session.py` |
| Session.add_assistant_message (text + tool_calls) | Done | `core/session.py` |
| Session.add_tool_result (call_id, content, is_error) | Done | `core/session.py` |
| Session.to_api_messages() (OpenAI format) | Done | `core/session.py` |
| Session._repair_dangling_calls() (4-step) | Done | `core/session.py` |
| Session.prompt_count / estimated_tokens properties | Done | `core/session.py` |
| HookDecision enum (ALLOW/DENY/MODIFY) | Done | `core/agent.py` |
| AgentHook protocol (5 methods, runtime_checkable) | Done | `core/agent.py` |
| Agent.__init__ (session, client, tools, max_turns, hooks) | Done | `core/agent.py` |
| Agent.run() - async generator with StreamEvent yield | Done | `core/agent.py` |
| Agent.run() - tool_call_delta accumulation by index | Done | `core/agent.py` |
| Agent.run() - hook call points (session_start/turn_start/turn_end) | Done | `core/agent.py` |
| Agent._execute_one_tool() - JSON parse + hook chain | Done | `core/agent.py` |
| Agent._execute_one_tool() - defensive exception handling (Layer 2) | Done | `core/agent.py` |
| MaxTurnsReached raised after max_turns exhausted | Done | `core/agent.py` |

**Progress: 16/16 items complete**

---

## Design Decisions

- No Actor pattern: Python asyncio single-thread, no concurrent mutation
- _repair_dangling_calls: equivalent to Rust's ensure_conversation_integrity()
- AgentHook is THE ONLY extension point (open-closed principle)
- Two-layer hook exception defense: Layer 1 (hook self-catch) + Layer 2 (Agent fallback)
- on_tool_call exception -> DENY (fail-safe); other hooks -> no-op (degrade)
- Tool definitions fetched every turn (not cached) for MCP hot-reload
- MODIFY hooks modify args in-place; registration order = modification priority
