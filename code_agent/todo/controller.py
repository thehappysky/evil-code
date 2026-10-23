# [Phase 5] 2026-08-04: TodoController — shared in-memory todo store
"""Shared todo controller used by the tool and the hook."""

from __future__ import annotations

from code_agent.todo.state import TodoItem, TodoState


class TodoController:
    """Holds the session todo state.

    The same instance is shared by ``TodoWriteTool`` (mutations) and
    ``TodoHook`` (turn-start reminders + nudge), so the model's todo list
    survives across turns/compaction without living in the conversation.
    """

    def __init__(self) -> None:
        self._state = TodoState()

    def state(self) -> TodoState:
        return self._state

    def snapshot(self) -> list[TodoItem]:
        return self._state.snapshot()

    def clear(self) -> None:
        self._state.clear()
