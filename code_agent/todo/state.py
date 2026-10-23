# [Phase 5] 2026-08-04: TodoState — structured task list (external state machine)
"""Pure todo state logic (no I/O, no Agent/Session dependency).

References:
    - Rust TodoState / TodoWrite (xai-grok-tools)
    - design-docs/Python-Agent-补足方案-设计.md §Phase 5

Design: the todo list is an *external* structured state machine — the model
does not need to remember it. ``TodoWrite`` replaces/merges the list; a hook
re-injects it as a reminder each turn.
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable

from pydantic import BaseModel, Field


class TodoStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TodoPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TodoItem(BaseModel):
    """A single todo entry."""

    id: str = Field(description="Stable identifier (unique across the list).")
    content: str = Field(description="Human-readable task description.")
    status: TodoStatus = TodoStatus.PENDING
    priority: TodoPriority = TodoPriority.MEDIUM


class TodoState:
    """Ordered todo store keyed by id.

    Pure Python — callers (controller/tool/hook) manage concurrency and I/O.
    """

    def __init__(self) -> None:
        self._items: dict[str, TodoItem] = {}

    # ── Mutations ──────────────────────────────────────────────────────────

    def apply_replace(self, todos: Iterable[TodoItem]) -> None:
        """Replace the whole list (TodoWrite merge=False)."""
        self._items = {t.id: t for t in todos}

    def apply_merge(self, todos: Iterable[TodoItem]) -> None:
        """Merge upserts into the list (TodoWrite merge=True).

        An existing id updates only the fields explicitly provided
        (status/priority/content when not None); a new id is inserted.
        """
        for incoming in todos:
            existing = self._items.get(incoming.id)
            if existing is None:
                self._items[incoming.id] = incoming
                continue
            updates = {}
            if incoming.content:
                updates["content"] = incoming.content
            if incoming.status is not TodoStatus.PENDING:
                updates["status"] = incoming.status
            if incoming.priority is not TodoPriority.MEDIUM:
                updates["priority"] = incoming.priority
            self._items[incoming.id] = (
                existing.model_copy(update=updates) if updates else existing
            )

    def clear(self) -> None:
        self._items.clear()

    @staticmethod
    def validate_no_duplicate_ids(todos: Iterable[TodoItem]) -> None:
        """Raise ValueError when the input list carries duplicate ids."""
        ids = [t.id for t in todos]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate todo ids in a single write")

    # ── Queries ────────────────────────────────────────────────────────────

    @property
    def items(self) -> list[TodoItem]:
        return list(self._items.values())

    def pending_count(self) -> int:
        return sum(
            1 for t in self._items.values()
            if t.status in (TodoStatus.PENDING, TodoStatus.IN_PROGRESS)
        )

    def snapshot(self) -> list[TodoItem]:
        return list(self._items.values())


def summarize_todo_state(items: Iterable[TodoItem]) -> str:
    """Render the todo list as a compact model-visible block."""
    lines = []
    for t in items:
        lines.append(f"- [{t.status.value}] ({t.priority}) {t.content}")
    if not lines:
        return "(no todos)"
    return "\n".join(lines)
