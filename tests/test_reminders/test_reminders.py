"""Tests for core/reminders.py — ReminderInjector."""

import pytest

from code_agent.prompts.reminders import ReminderInjector


class TestCurrentDateReminder:
    def test_contains_date(self):
        inj = ReminderInjector()
        reminder = inj.current_date_reminder()
        assert "<system-reminder>" in reminder
        assert "Today's date is" in reminder
        assert "2026" in reminder  # Current year


class TestTodoReminder:
    def test_pending_todos(self):
        inj = ReminderInjector()
        todos = [
            {"subject": "Write tests", "status": "pending"},
            {"subject": "Fix bug", "status": "in_progress"},
            {"subject": "Done task", "status": "completed"},
        ]
        reminder = inj.todo_reminder(todos)
        assert reminder is not None
        assert "Write tests" in reminder
        assert "Fix bug" in reminder
        assert "Done task" not in reminder

    def test_no_pending(self):
        inj = ReminderInjector()
        reminder = inj.todo_reminder([
            {"subject": "Done", "status": "completed"},
        ])
        assert reminder is None

    def test_empty_list(self):
        inj = ReminderInjector()
        assert inj.todo_reminder([]) is None

    def test_max_10_items(self):
        inj = ReminderInjector()
        todos = [
            {"subject": f"Task {i}", "status": "pending"}
            for i in range(15)
        ]
        reminder = inj.todo_reminder(todos)
        assert reminder is not None
        assert "and 5 more tasks" in reminder


class TestDateRolloverReminder:
    def test_first_call_returns_reminder(self):
        inj = ReminderInjector()
        result = inj.date_rollover_reminder()
        assert result is not None
        assert "Today's date is" in result

    def test_second_call_same_day_returns_none(self):
        inj = ReminderInjector()
        result1 = inj.date_rollover_reminder()
        assert result1 is not None
        result2 = inj.date_rollover_reminder()
        assert result2 is None  # Already announced today


class TestCompactionReminder:
    def test_empty(self):
        inj = ReminderInjector()
        result = inj.compaction_reminder()
        assert "<system-reminder>" in result
        assert "compacted" in result

    def test_with_edited_files(self):
        inj = ReminderInjector()
        result = inj.compaction_reminder(
            edited_files=["/src/main.py", "/tests/test_main.py"],
        )
        assert "/src/main.py" in result
        assert "/tests/test_main.py" in result

    def test_with_running_tasks(self):
        inj = ReminderInjector()
        result = inj.compaction_reminder(
            running_tasks=["Background test runner", "File watcher"],
        )
        assert "Background test runner" in result

    def test_with_connected_servers(self):
        inj = ReminderInjector()
        result = inj.compaction_reminder(
            connected_servers=["mcp-server-1", "mcp-server-2"],
        )
        assert "mcp-server-1" in result

    def test_all_fields(self):
        inj = ReminderInjector()
        result = inj.compaction_reminder(
            edited_files=["/a.py"],
            running_tasks=["test"],
            connected_servers=["mcp"],
        )
        assert "Edited files" in result
        assert "Running background tasks" in result
        assert "Connected MCP servers" in result


class TestMemoryReminder:
    def test_with_results(self):
        inj = ReminderInjector()
        results = [
            {"path": "MEMORY.md", "text": "Use PEP 8.", "source": "workspace"},
            {"path": "global.md", "text": "Dark mode preferred.", "source": "global"},
        ]
        reminder = inj.memory_reminder(results)
        assert reminder is not None
        assert "<system-reminder>" in reminder
        assert "Use PEP 8" in reminder
        assert "Dark mode preferred" in reminder

    def test_empty_results(self):
        inj = ReminderInjector()
        assert inj.memory_reminder([]) is None

    def test_none_results(self):
        inj = ReminderInjector()
        assert inj.memory_reminder([]) is None

    def test_max_5_results(self):
        inj = ReminderInjector()
        results = [
            {"path": f"file{i}.md", "text": f"Content {i}", "source": "workspace"}
            for i in range(10)
        ]
        reminder = inj.memory_reminder(results)
        assert reminder is not None
        assert "and 5 more memories" in reminder


class TestInterruptReminder:
    def test_format(self):
        inj = ReminderInjector()
        result = inj.interrupt_reminder()
        assert "Request interrupted by user" in result
        assert "<system-reminder>" in result


class TestBackgroundTasksReminder:
    def test_with_tasks(self):
        inj = ReminderInjector()
        result = inj.background_tasks_reminder(["Test runner", "Linter"])
        assert result is not None
        assert "Test runner" in result

    def test_empty(self):
        inj = ReminderInjector()
        assert inj.background_tasks_reminder([]) is None
