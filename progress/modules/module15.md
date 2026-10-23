# Phase 2 Module 4: core/reminders.py — System-Reminder Injector

> Phase: 2 | Status: DONE | Created: 2026-07-25

---

## Scope

| Feature | Description |
|:---|:---|
| ReminderInjector | Generates <system-reminder> blocks at key conversation lifecycle points |
| current_date_reminder | Inject today's date so model doesn't rely on training data cutoff |
| todo_reminder | List pending tasks; returns None if all completed |
| date_rollover_reminder | One-shot per calendar day date announcement |
| compaction_reminder | Post-compaction state summary (edited files, running tasks, MCP servers) |
| memory_reminder | Format memory search results as <memory-context> block |
| interrupt_reminder | "Request interrupted by user" notice for next turn |
| background_tasks_reminder | Running background task summaries |

---

## Final Architecture

```
code_agent/core/
├── reminders.py         # ReminderInjector: 8 reminder generators (146 lines)
```

---

## Implementation Status

| Item | Status | File |
|:---|:---|:---|
| ReminderInjector class | Done | `core/reminders.py` |
| current_date_reminder() — "Today's date is YYYY-MM-DD." | Done | `core/reminders.py` |
| todo_reminder() — filter pending, format numbered list, None if empty | Done | `core/reminders.py` |
| date_rollover_reminder() — one-shot per day, stateful | Done | `core/reminders.py` |
| compaction_reminder() — edited files + running tasks + MCP servers | Done | `core/reminders.py` |
| memory_reminder() — <memory-context> with up to 5 results, None if empty | Done | `core/reminders.py` |
| interrupt_reminder() — user cancelled notice | Done | `core/reminders.py` |
| background_tasks_reminder() — running task list, None if empty | Done | `core/reminders.py` |

**Progress: 8/8 items complete**

---

## Design Decisions

- **All reminders return str | None**: None means "nothing to inject" — caller skips injection
- **Stateful date tracking**: _last_date prevents redundant daily reminders
- **5-result cap for memory**: Avoids crowding context with low-relevance results
- **Formatter, not injector**: ReminderInjector produces strings; hooks/caller decide when to inject
