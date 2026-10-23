# Module 5: Tool Infrastructure

> Phase: 1 | Status: DONE | Created: 2026-07-19

---

## Scope

| Feature | Description |
|:---|:---|
| ToolResult | Unified return type (output + is_error + truncated), ok()/error() factories |
| ToolDefinition | name + description + input_model + handler + to_openai_schema() |
| ToolRegistry | Decorator register + finalize lock + execute (4-step) + unified truncation |

---

## Final Architecture

```
code_agent/tools/
├── __init__.py      # exports: ToolRegistry, ToolDefinition, ToolResult
├── result.py        # ToolResult (Pydantic BaseModel)
├── definition.py    # ToolDefinition (to_openai_schema from Pydantic JSON Schema)
└── registry.py      # ToolRegistry (register/finalize/execute/get_definitions)
```

---

## Implementation Status

| Item | Status | File |
|:---|:---|:---|
| ToolResult (output, is_error, truncated) | Done | `tools/result.py` |
| ToolResult.ok() / ToolResult.error() class methods | Done | `tools/result.py` |
| ToolDefinition (name, description, input_model, handler, category) | Done | `tools/definition.py` |
| ToolDefinition.to_openai_schema() (strips title/$defs) | Done | `tools/definition.py` |
| ToolRegistry.register() decorator | Done | `tools/registry.py` |
| ToolRegistry.finalize() lock | Done | `tools/registry.py` |
| ToolRegistry.get_definitions() (every turn, not cached) | Done | `tools/registry.py` |
| ToolRegistry.execute() (lookup->validate->call->truncate) | Done | `tools/registry.py` |
| ToolRegistry._maybe_truncate() (max_output_chars=100K) | Done | `tools/registry.py` |
| ToolRegistry.list_names() | Done | `tools/registry.py` |

**Progress: 10/10 items complete**

---

## Design Decisions

- Decorator registration: equivalent to Rust's register::<T>()
- finalize() lock: equivalent to Rust's FinalizedToolset immutability
- Pydantic model_validate() replaces Rust's serde_path_to_error
- Unified truncation in registry (not per-tool) — single responsibility
- get_definitions() called every turn (not cached) for MCP tool hot-reload
- execute() catches all exceptions -> ToolResult.error (never crashes Agent)
- max_output_chars default 100K (configurable via constructor)
