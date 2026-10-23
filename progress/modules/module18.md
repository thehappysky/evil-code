# Phase 2 Module 7: Session.replace_history() + from_dicts()

> Phase: 2 | Status: DONE | Created: 2026-07-25

---

## Scope

| Feature | Description |
|:---|:---|
| replace_history() | Bulk replace entire conversation history after compaction |
| from_dicts() | Reconstruct Session from stored JSONL message dicts (session resume) |

---

## Implementation Status

| Item | Status | File |
|:---|:---|:---|
| replace_history() — validate non-empty + SystemMessage first | Done | `core/session.py` |
| replace_history() — recalculate token estimate | Done | `core/session.py` |
| replace_history() — repair dangling calls in new history | Done | `core/session.py` |
| from_dicts() — reconstruct Session from list of message dicts | Done | `core/session.py` |
| from_dicts() — handle all 4 message types (system/user/assistant/tool) | Done | `core/session.py` |
| from_dicts() — optional system_prompt override | Done | `core/session.py` |
| from_dicts() — recalculate token estimate after reconstruction | Done | `core/session.py` |

**Progress: 7/7 items complete**

---

## Design Decisions

- **replace_history validates structure**: Requires non-empty and SystemMessage first — catches assembly bugs early
- **Why not direct _history = compacted?**: Direct assignment bypasses token estimation, dangling check — replace_history() is the unified entry point
- **from_dicts handles OpenAI-format tool_calls**: Parses `tc["function"]["name"]` pattern (OpenAI) and `tc["name"]` (simplified)
