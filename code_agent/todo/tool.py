# [Phase 5] 2026-08-04: TodoWrite tool — the model's handle to the todo list
"""TodoWrite tool.

References:
    - Rust TodoWriteTool (xai-grok-tools)
    - design-docs/Python-Agent-补足方案-设计.md §Phase 5

The tool mutates a shared ``TodoState`` (via ``TodoController``) and returns
the rendered list so the model sees its own progress immediately. The same
state is re-injected as a reminder each turn by ``TodoHook``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from code_agent.todo.state import TodoItem, TodoPriority, TodoState, TodoStatus, summarize_todo_state


class TodoUpdate(BaseModel):
    """One todo entry in a TodoWrite request."""

    id: str = Field(description="Stable identifier for this todo.")
    content: str | None = Field(default=None, description="Task description (omitted to keep existing).")
    status: TodoStatus | None = Field(default=None, description="Status update (omitted to keep existing).")
    priority: TodoPriority | None = Field(default=None, description="Priority (omitted to keep existing).")


class TodoWriteInput(BaseModel):
    """Input for the todo_write tool."""

    merge: bool = Field(
        default=True,
        description="True to merge/update the existing list; False to replace it entirely.",
    )
    todos: list[TodoUpdate] = Field(description="Todos to write.")


class TodoWriteTool:
    """Handle for the ``todo_write`` tool bound to a TodoController."""

    def __init__(self, controller):
        self._controller = controller

    async def run(self, input: TodoWriteInput) -> str:
        """Apply the write and return the rendered list for the model.

        Args:
            input: TodoWriteInput.

        Returns:
            The full todo list text (so the model sees current state).

        Raises:
            ValueError: On duplicate ids in a single write.
        """
        items = [
            TodoItem(
                id=u.id,
                content=u.content or "",
                status=u.status or TodoStatus.PENDING,
                priority=u.priority or TodoPriority.MEDIUM,
            )
            for u in input.todos
        ]
        TodoState.validate_no_duplicate_ids(items)

        state = self._controller.state()
        if input.merge:
            state.apply_merge(items)
        else:
            state.apply_replace(items)

        return f"<todos>\n{summarize_todo_state(state.items)}\n</todos>"
