# [PRM-01] 2026-08-09: Moved from code_agent/core/reminders.py → code_agent/prompts/reminders.py.
# <system-reminder> text generation is a 指令层 concern, not a core/ concern.
"""System-reminder injection into conversation.

References:
    - Rust reminders.rs (session/acp_session_impl/reminders.rs)

Injects <system-reminder> blocks as synthetic user messages.
Each reminder is wrapped in <system-reminder>...</system-reminder> tags.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


class ReminderInjector:
    """System-reminder injection into conversation.

    Injects <system-reminder> blocks as synthetic user messages at
    key points in the conversation lifecycle:

    - Session start: memory context, current date, ongoing work
    - Turn start: todos, task progress
    - Post-compaction: edited files, running tasks
    - Post-interrupt: user cancelled notice

    Usage::

        injector = ReminderInjector()
        reminder = injector.memory_reminder(search_results)
        if reminder:
            session.add_user_message(reminder)
    """

    def __init__(self):
        self._last_date: str = ""

    # ── Reminder generators ─────────────────────────────────────────────────

    def current_date_reminder(self) -> str:
        """Generate reminder with current date.

        Format: "Today's date is YYYY-MM-DD."

        Injected at session start so the model knows the current date
        without relying on training data cutoff.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return (
            f"<system-reminder>\n"
            f"Today's date is {today}.\n"
            f"</system-reminder>"
        )

    def todo_reminder(self, todos: list[dict[str, Any]]) -> str | None:
        """Generate reminder if there are unfinished todos.

        Args:
            todos: List of todo dicts with keys: subject, status, description.

        Returns:
            Formatted reminder string, or None if no pending todos.
        """
        pending = [t for t in todos if t.get("status") not in ("completed", "deleted")]

        if not pending:
            return None

        lines = ["<system-reminder>", "You have pending tasks:"]
        for i, todo in enumerate(pending[:10], 1):
            subject = todo.get("subject", "Unnamed task")
            status = todo.get("status", "pending")
            lines.append(f"  {i}. [{status}] {subject}")

        if len(pending) > 10:
            lines.append(f"  ... and {len(pending) - 10} more tasks")

        lines.append("</system-reminder>")
        return "\n".join(lines)

    def date_rollover_reminder(self) -> str | None:
        """Generate date reminder if local date changed since last announcement.

        One-shot per calendar day. Returns None if already announced today.

        Returns:
            Date reminder string, or None.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if today == self._last_date:
            return None

        self._last_date = today
        return self.current_date_reminder()

    def compaction_reminder(
        self,
        edited_files: list[str] | None = None,
        running_tasks: list[str] | None = None,
        connected_servers: list[str] | None = None,
    ) -> str:
        """Generate post-compaction state summary.

        Informs the model about what happened before compaction so it
        doesn't lose track of state.

        Args:
            edited_files: List of file paths that were edited.
            running_tasks: List of background task descriptions.
            connected_servers: List of connected MCP server names.

        Returns:
            Formatted compaction reminder string.
        """
        lines = [
            "<system-reminder>",
            "The conversation was compacted to save context. "
            "Here is what you were working on:",
        ]

        if edited_files:
            lines.append("")
            lines.append("Edited files:")
            for f in edited_files[:20]:
                lines.append(f"  - {f}")
            if len(edited_files) > 20:
                lines.append(f"  ... and {len(edited_files) - 20} more files")

        if running_tasks:
            lines.append("")
            lines.append("Running background tasks:")
            for t in running_tasks[:10]:
                lines.append(f"  - {t}")

        if connected_servers:
            lines.append("")
            lines.append("Connected MCP servers:")
            for s in connected_servers[:10]:
                lines.append(f"  - {s}")

        lines.append("")
        lines.append("Continue helping the user with their original request.")
        lines.append("</system-reminder>")
        return "\n".join(lines)

    def memory_reminder(
        self, results: list[dict[str, Any]]
    ) -> str | None:
        """Format memory search results as <memory-context> block.

        Injected once per session at start of first turn.

        Args:
            results: List of SearchResult-compatible dicts with keys:
                     path, text, source.

        Returns:
            Formatted memory context string, or None if no results.
        """
        if not results:
            return None

        lines = [
            "<system-reminder>",
            "The following memories were found that may be relevant to "
            "the current conversation.",
        ]

        for i, r in enumerate(results[:5], 1):
            source = r.get("source", "unknown")
            path = r.get("path", "")
            text = r.get("text", "")[:500]
            lines.append("")
            lines.append(f"[Memory {i}] ({source}) {path}")
            lines.append(text)

        if len(results) > 5:
            lines.append(f"\n... and {len(results) - 5} more memories")

        lines.append("</system-reminder>")
        return "\n".join(lines)

    def interrupt_reminder(self) -> str:
        """Generate reminder after user cancelled mid-turn.

        Injected on next real user turn so the model knows the previous
        action was interrupted.

        Returns:
            Interrupt reminder string.
        """
        return (
            "<system-reminder>\n"
            "[Request interrupted by user]\n"
            "The previous request was cancelled before completion. "
            "Any pending tool calls were not executed.\n"
            "</system-reminder>"
        )

    def background_tasks_reminder(
        self, task_summaries: list[str]
    ) -> str | None:
        """Generate reminder about running background tasks.

        Args:
            task_summaries: Brief descriptions of running tasks.

        Returns:
            Formatted reminder, or None if no tasks.
        """
        if not task_summaries:
            return None

        lines = [
            "<system-reminder>",
            "The following background tasks are still running:",
        ]
        for t in task_summaries:
            lines.append(f"  - {t}")
        lines.append("</system-reminder>")
        return "\n".join(lines)
