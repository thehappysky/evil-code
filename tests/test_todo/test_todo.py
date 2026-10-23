# [Phase 5] 2026-08-04: Tests for the todo system
"""Tests for code_agent/todo/ — state, tool, hook."""

from __future__ import annotations

import pytest

from code_agent.todo.controller import TodoController
from code_agent.todo.hook import TodoHook
from code_agent.todo.state import TodoItem, TodoPriority, TodoState, TodoStatus
from code_agent.todo.tool import TodoWriteInput, TodoWriteTool
from code_agent.tools.result import ToolResult


class TestTodoState:
    def test_apply_replace(self):
        state = TodoState()
        state.apply_replace([
            TodoItem(id="a", content="A"),
            TodoItem(id="b", content="B"),
        ])
        assert state.pending_count() == 2

    def test_apply_merge_updates_existing(self):
        state = TodoState()
        state.apply_replace([TodoItem(id="a", content="A")])
        state.apply_merge([TodoItem(id="a", content="A2", status=TodoStatus.COMPLETED)])
        item = state.items[0]
        assert item.content == "A2"
        assert item.status is TodoStatus.COMPLETED

    def test_apply_merge_inserts_new(self):
        state = TodoState()
        state.apply_merge([TodoItem(id="a", content="A")])
        assert len(state.items) == 1

    def test_duplicate_ids_rejected(self):
        state = TodoState()
        with pytest.raises(ValueError):
            state.validate_no_duplicate_ids([
                TodoItem(id="a", content="1"),
                TodoItem(id="a", content="2"),
            ])

    def test_clear(self):
        state = TodoState()
        state.apply_replace([TodoItem(id="a", content="A")])
        state.clear()
        assert state.items == []


class TestTodoWriteTool:
    def test_write_returns_rendered_list(self):
        ctrl = TodoController()
        tool = TodoWriteTool(ctrl)
        import asyncio

        out = asyncio.run(tool.run(TodoWriteInput(
            todos=[{"id": "t1", "content": "Step one", "status": "in_progress"}],
        )))
        assert "Step one" in out
        assert "in_progress" in out
        assert ctrl.snapshot()[0].id == "t1"

    def test_merge_vs_replace(self):
        ctrl = TodoController()
        tool = TodoWriteTool(ctrl)
        import asyncio

        asyncio.run(tool.run(TodoWriteInput(todos=[{"id": "t1", "content": "one"}])))
        asyncio.run(tool.run(TodoWriteInput(todos=[{"id": "t2", "content": "two"}])))
        assert {t.id for t in ctrl.snapshot()} == {"t1", "t2"}

        asyncio.run(tool.run(TodoWriteInput(
            merge=False, todos=[{"id": "t3", "content": "three"}],
        )))
        assert {t.id for t in ctrl.snapshot()} == {"t3"}

    def test_duplicate_ids_in_write_rejected(self):
        tool = TodoWriteTool(TodoController())
        import asyncio

        with pytest.raises(ValueError):
            asyncio.run(tool.run(TodoWriteInput(todos=[
                {"id": "x", "content": "1"},
                {"id": "x", "content": "2"},
            ])))


class TestTodoHook:
    def _hook_with_todos(self):
        from code_agent.core.session import Session

        ctrl = TodoController()
        ctrl.state().apply_replace([
            TodoItem(id="t1", content="Step one"),
            TodoItem(id="t2", content="Step two"),
        ])
        hook = TodoHook(ctrl)
        return hook, ctrl, Session(system_prompt="T")

    async def test_turn_start_injects_reminder(self):
        hook, ctrl, session = self._hook_with_todos()
        await hook.on_turn_start(session, 0, [])
        texts = [m.content for m in session.messages if m.role == "user"]
        assert any("Step one" in t and "Step two" in t for t in texts)

    async def test_no_injection_when_no_todos(self):
        from code_agent.core.session import Session

        hook = TodoHook(TodoController())
        session = Session(system_prompt="T")
        await hook.on_turn_start(session, 0, [])
        assert len(session.messages) == 1  # only system

    async def test_todo_write_resets_nudge(self):
        from code_agent.core.session import Session

        ctrl = TodoController()
        ctrl.state().apply_replace([TodoItem(id="t1", content="Step")])
        hook = TodoHook(ctrl)
        session = Session(system_prompt="T")

        # Several turns without a todo_write → nudge fires.
        for turn in range(6):
            await hook.on_turn_start(session, turn, [])
        texts = [m.content for m in session.messages if m.role == "user"]
        assert any("todo_write" in t for t in texts)

        # A todo_write resets the counter so the nudge stops.
        await hook.on_tool_result("todo_write", {}, ToolResult.ok("ok"))
        session2 = Session(system_prompt="T")
        await hook.on_turn_start(session2, 0, [])
        texts2 = [m.content for m in session2.messages if m.role == "user"]
        assert not any("todo_write" in t for t in texts2)
