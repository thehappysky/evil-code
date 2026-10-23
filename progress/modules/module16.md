# Phase 2 Module 5: hooks/ — Phase 2 AgentHook Implementations

> Phase: 2 | Status: DONE | Created: 2026-07-25

---

## Scope

| Feature | Description |
|:---|:---|
| MemoryHook | Wraps MemoryIndex; on_session_start injects cross-session memory as synthetic user message |
| CompactionHook | Wraps CompactionPolicy + ChatClient; on_turn_start triggers summary + history replacement |
| PersistenceHook | Wraps SessionStore; on_turn_end persists session to JSONL |
| EditTrackerHook | Tracks file_path from search_replace/write_file tool results |

---

## Final Architecture

```
code_agent/hooks/
├── __init__.py           # exports: MemoryHook, CompactionHook, PersistenceHook, EditTrackerHook
└── phase2_hooks.py       # 4 AgentHook implementations (269 lines)
```

---

## Implementation Status

| Item | Status | File |
|:---|:---|:---|
| MemoryHook — on_session_start searches memory + injects <memory-context> | Done | `hooks/phase2_hooks.py` |
| MemoryHook — _injected flag prevents duplicate injections | Done | `hooks/phase2_hooks.py` |
| CompactionHook — on_turn_start: should_compact -> generate_summary -> assemble -> replace_history | Done | `hooks/phase2_hooks.py` |
| CompactionHook — token budget splitting: to_summarize vs to_preserve | Done | `hooks/phase2_hooks.py` |
| CompactionHook — dict-to-Message conversion for assembled history | Done | `hooks/phase2_hooks.py` |
| CompactionHook — compaction_count tracking | Done | `hooks/phase2_hooks.py` |
| PersistenceHook — on_session_start: init session with SessionMeta | Done | `hooks/phase2_hooks.py` |
| PersistenceHook — on_turn_end: append new messages + update meta | Done | `hooks/phase2_hooks.py` |
| PersistenceHook — incremental writes: only new messages appended | Done | `hooks/phase2_hooks.py` |
| EditTrackerHook — on_tool_result: record file_path from search_replace/write_file | Done | `hooks/phase2_hooks.py` |
| EditTrackerHook — get_edited_files() / clear() methods | Done | `hooks/phase2_hooks.py` |
| All hooks — exception handling: log.warning + continue (degrade gracefully) | Done | `hooks/phase2_hooks.py` |

**Progress: 12/12 items complete**

---

## Design Decisions

- **CompactionHook preserves last N turns**: Uses count_preserved_turns() with token budget from CompactionPolicy.target_budget()
- **PersistenceHook incremental writes**: Counts existing messages, only appends new ones
- **EditTrackerHook ignores shell_exec**: False-positive tracking (ls > file) is more dangerous than missing shell-based edits
- **All hooks degrade gracefully**: Any hook failure → log.warning + Agent continues without that feature
