from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from rich.text import Text

from code_agent.tui.domain import ToolFeedbackState, UiLanguage, redact_sensitive_text
from code_agent.tui.text import ui_text
from code_agent.tui.widgets import ToolFeedbackMessage

_TOOL_OUTPUT_MAX_LINES = 40
_TOOL_OUTPUT_MAX_CHARACTERS = 6_000
_TOOL_DIFF_MAX_FILES = 8
_COMPACT_READ_TOOLS = frozenset({"grep", "list_dir", "read_file", "skill", "view_image"})
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


class ToolFeedbackRenderer:
    """Renders tool-call feedback cards for the Evil Code TUI.

    The renderer owns only the *rendering* of tool feedback: it builds the
    Rich ``Text`` bodies and configures tool-feedback widgets. Ownership of
    transcript state (entries, call-id lookups, the widget tree) remains with
    ``EvilCodeApp``, which delegates rendering here.
    """

    def __init__(self, get_language: Callable[[], UiLanguage]) -> None:
        self._get_language = get_language

    @property
    def _language(self) -> UiLanguage:
        return self._get_language()

    @staticmethod
    def safe_tool_text(value: str) -> str:
        normalized = _ANSI_ESCAPE.sub("", value.replace("\r\n", "\n").replace("\r", "\n"))
        printable = "".join(
            character if character in {"\n", "\t"} or ord(character) >= 32 else "�"
            for character in normalized
        )
        return redact_sensitive_text(printable)

    @staticmethod
    def bounded_inline(value: object, *, limit: int = 140) -> str:
        if not isinstance(value, str) or not value:
            return "—"
        rendered = " ".join(ToolFeedbackRenderer.safe_tool_text(value).split())
        return rendered if len(rendered) <= limit else f"{rendered[: limit - 1]}…"

    @classmethod
    def tool_invocation(cls, name: str, raw_arguments: object) -> str:
        if not isinstance(raw_arguments, Mapping):
            return name
        preferred_keys = {
            "bash": ("command",),
            "read_file": ("path",),
            "list_dir": ("path",),
            "grep": ("query", "path"),
            "search_replace": ("path",),
            "apply_patch": ("path",),
            "task_output": ("task_id",),
        }.get(name, ("path", "query", "pattern", "task_id"))
        values = [
            cls.bounded_inline(raw_arguments.get(key), limit=100)
            for key in preferred_keys
            if isinstance(raw_arguments.get(key), str) and raw_arguments.get(key)
        ]
        return f"{name}({', '.join(values)})" if values else name

    def render_body(self, state: ToolFeedbackState) -> Text:
        compact_summary = self.compact_read_summary(state)
        if compact_summary is not None and not state.expanded:
            body = Text(overflow="fold")
            marker_style = "bold #78c2a4" if state.phase == "completed" else "bold #8b9cff"
            body.append("● ", style=marker_style)
            body.append(compact_summary, style="#c5c9ce")
            if state.phase == "completed" and state.duration is not None:
                body.append(f" · {state.duration}", style="#7f8790")
            return body

        body = Text(overflow="fold")
        body.append("● ", style="bold #78c2a4")
        if state.hosted:
            body.append(ui_text(self._language, "tool.card.hosted"), style="#9aa3b2")
            body.append(" ")
        invocation = self.tool_invocation(state.name, state.arguments)
        body.append(invocation, style="bold #8ed1e6")

        if state.permission_effect == "allow":
            self.append_tool_line(
                body,
                "├",
                ui_text(
                    self._language,
                    "tool.card.allowed",
                    reason=self.bounded_inline(state.permission_reason),
                ),
                style="#9fb8af",
            )
        elif state.permission_effect == "ask":
            if state.approval_outcome is not None:
                outcome = ui_text(
                    self._language,
                    f"tool.approval.{self.known_approval_outcome(state.approval_outcome)}",
                )
                self.append_tool_line(
                    body,
                    "├",
                    ui_text(self._language, "tool.card.approval", outcome=outcome),
                    style="#9fb8af" if state.approval_effect == "allow" else "#d58b8b",
                )
            elif state.phase == "awaiting_approval":
                self.append_tool_line(
                    body,
                    "├",
                    ui_text(self._language, "tool.card.awaiting_approval"),
                    style="#a9adb3",
                )
            else:
                self.append_tool_line(
                    body,
                    "├",
                    ui_text(
                        self._language,
                        "tool.card.approval_required",
                        reason=self.bounded_inline(state.permission_reason),
                    ),
                    style="#a9adb3",
                )

        change_report = self.tool_change_report(state)
        details_available = self.tool_details_available(state, change_report)
        if state.content is not None and (state.content or change_report is None):
            self.append_tool_output(body, state, expanded=state.expanded)
        if change_report is not None:
            self.append_tool_changes(body, change_report, expanded=state.expanded)
        if details_available:
            self.append_tool_line(
                body,
                "├",
                ui_text(
                    self._language,
                    (
                        "tool.card.details_expanded"
                        if state.expanded
                        else "tool.card.details_collapsed"
                    ),
                ),
                style="#8b9cff",
            )

        if state.phase == "running":
            self.append_tool_line(
                body,
                "├",
                ui_text(self._language, "tool.card.running"),
                style="#a9adb3",
            )
        if state.phase == "completed":
            self.append_tool_line(
                body,
                "└",
                ui_text(
                    self._language,
                    "tool.card.completed",
                    duration=state.duration or "—",
                ),
                style="#78c2a4",
            )
        elif state.phase == "failed":
            self.append_tool_line(
                body,
                "└",
                ui_text(
                    self._language,
                    "tool.card.failed",
                    duration=state.duration or "—",
                ),
                style="#d58b8b",
            )
        elif state.phase in {"permission_denied", "approval_denied"}:
            reason = state.approval_reason or state.permission_reason
            self.append_tool_line(
                body,
                "└",
                ui_text(
                    self._language,
                    "tool.card.denied",
                    reason=self.bounded_inline(reason),
                ),
                style="#d58b8b",
            )
        return body

    def compact_read_summary(self, state: ToolFeedbackState) -> str | None:
        if (
            state.hosted
            or state.name not in _COMPACT_READ_TOOLS
            or state.is_error
            or state.phase
            in {"failed", "permission_denied", "approval_denied", "awaiting_approval"}
        ):
            return None
        key = "completed" if state.phase == "completed" else "running"
        if state.name == "grep":
            query = self.bounded_inline(state.arguments.get("query"), limit=80)
            path = self.bounded_inline(state.arguments.get("path"), limit=80)
            return ui_text(
                self._language,
                f"tool.compact.grep.{key}",
                query=query,
                path=path,
            )
        target_key = "name" if state.name == "skill" else "path"
        target = self.bounded_inline(state.arguments.get(target_key), limit=120)
        return ui_text(
            self._language,
            f"tool.compact.{state.name}.{key}",
            target=target,
        )

    @staticmethod
    def append_tool_line(body: Text, connector: str, content: str, *, style: str) -> None:
        body.append("\n")
        body.append(f"{connector} ", style="#626a73")
        body.append(content, style=style)

    @staticmethod
    def known_approval_outcome(outcome: str) -> str:
        return outcome if outcome in {"allow_once", "allow_session", "deny"} else "unknown"

    @classmethod
    def tool_details_available(
        cls,
        state: ToolFeedbackState,
        change_report: Mapping[str, Any] | None,
    ) -> bool:
        if state.content is not None and cls.safe_tool_text(state.content).strip():
            return True
        if change_report is None:
            return False
        raw_files = change_report.get("files")
        if not isinstance(raw_files, Sequence) or isinstance(raw_files, str | bytes):
            return False
        return any(
            isinstance(change, Mapping)
            and isinstance(change.get("diff"), str)
            and bool(cls.safe_tool_text(change["diff"]).strip())
            for change in raw_files
        )

    def configure_widget(
        self,
        widget: ToolFeedbackMessage,
        state: ToolFeedbackState,
    ) -> None:
        available = self.tool_details_available(state, self.tool_change_report(state))
        widget.can_focus = available
        widget.set_class(available, "tool-interactive")
        widget.set_class(available and state.expanded, "tool-expanded")
        widget.tooltip = (
            ui_text(
                self._language,
                ("tool.card.details_expanded" if state.expanded else "tool.card.details_collapsed"),
            )
            if available
            else None
        )

    def append_tool_output(
        self,
        body: Text,
        state: ToolFeedbackState,
        *,
        expanded: bool,
    ) -> None:
        content = state.content or ""
        lines, total_lines, omitted_lines, truncated = self.bounded_tool_preview(content)
        if not lines:
            heading = ui_text(
                self._language,
                "tool.card.error_empty" if state.is_error else "tool.card.output_empty",
            )
        else:
            heading = ui_text(
                self._language,
                "tool.card.error" if state.is_error else "tool.card.output",
                lines=total_lines,
            )
        self.append_tool_line(
            body,
            "├",
            heading,
            style="#d58b8b" if state.is_error else "#9aa3b2",
        )
        if not expanded:
            return
        for line in lines:
            body.append("\n│   ", style="#505860")
            body.append(line, style="#c5c9ce")
        if omitted_lines:
            body.append("\n│   ", style="#505860")
            body.append(
                ui_text(self._language, "tool.card.lines_omitted", count=omitted_lines),
                style="italic #7f8790",
            )
        if truncated:
            body.append("\n│   ", style="#505860")
            body.append(
                ui_text(self._language, "tool.card.preview_truncated"),
                style="italic #7f8790",
            )

    @classmethod
    def bounded_tool_preview(cls, content: str) -> tuple[tuple[str, ...], int, int, bool]:
        safe = cls.safe_tool_text(content)
        raw_lines = safe.splitlines()
        total_lines = len(raw_lines)
        omitted_lines = 0
        if total_lines > _TOOL_OUTPUT_MAX_LINES:
            head_count = _TOOL_OUTPUT_MAX_LINES - 10
            selected = [*raw_lines[:head_count], *raw_lines[-10:]]
            omitted_lines = total_lines - len(selected)
        else:
            selected = raw_lines

        rendered: list[str] = []
        characters = 0
        truncated = False
        for line in selected:
            remaining = _TOOL_OUTPUT_MAX_CHARACTERS - characters
            if remaining <= 0:
                truncated = True
                break
            if len(line) > remaining:
                rendered.append(f"{line[: max(0, remaining - 1)]}…")
                truncated = True
                break
            rendered.append(line)
            characters += len(line) + 1
        return tuple(rendered), total_lines, omitted_lines, truncated

    def tool_change_report(self, state: ToolFeedbackState) -> dict[str, Any] | None:
        if state.workspace_changes is not None:
            raw_files = state.workspace_changes.get("files")
            if isinstance(raw_files, Sequence) and not isinstance(raw_files, str | bytes):
                return state.workspace_changes
        if state.phase != "completed":
            return None
        if state.name == "search_replace":
            path = state.arguments.get("path")
            old = state.arguments.get("old")
            new = state.arguments.get("new")
            if isinstance(path, str) and isinstance(old, str) and isinstance(new, str):
                diff_lines = list(
                    difflib.unified_diff(
                        old.splitlines(),
                        new.splitlines(),
                        fromfile=f"a/{path}",
                        tofile=f"b/{path}",
                        lineterm="",
                        n=3,
                    )
                )
                return {
                    "files": [
                        {
                            "path": path,
                            "status": "modified",
                            "additions": sum(
                                line.startswith("+") and not line.startswith("+++")
                                for line in diff_lines
                            ),
                            "deletions": sum(
                                line.startswith("-") and not line.startswith("---")
                                for line in diff_lines
                            ),
                            "diff": "\n".join(diff_lines),
                            "diff_truncated": False,
                        }
                    ],
                    "omitted_files": 0,
                    "scan_limited": False,
                }
        if state.name == "apply_patch":
            patch = next(
                (
                    value
                    for key in ("patch", "input")
                    if isinstance(value := state.arguments.get(key), str) and value
                ),
                None,
            )
            if patch is not None:
                path = state.arguments.get("path")
                display_path = path if isinstance(path, str) and path else "patch"
                return {
                    "files": [
                        {
                            "path": display_path,
                            "status": "modified",
                            "additions": sum(
                                line.startswith("+") and not line.startswith("+++")
                                for line in patch.splitlines()
                            ),
                            "deletions": sum(
                                line.startswith("-") and not line.startswith("---")
                                for line in patch.splitlines()
                            ),
                            "diff": patch,
                            "diff_truncated": False,
                        }
                    ],
                    "omitted_files": 0,
                    "scan_limited": False,
                }
        return None

    def append_tool_changes(
        self,
        body: Text,
        report: Mapping[str, Any],
        *,
        expanded: bool,
    ) -> None:
        raw_files = report.get("files")
        if not isinstance(raw_files, Sequence) or isinstance(raw_files, str | bytes):
            return
        files = [item for item in raw_files if isinstance(item, Mapping)]
        visible = files[:_TOOL_DIFF_MAX_FILES]
        for change in visible:
            path = self.bounded_inline(change.get("path"), limit=180)
            status = change.get("status")
            additions = self.non_negative_int(change.get("additions"))
            deletions = self.non_negative_int(change.get("deletions"))
            status_key = status if status in {"created", "modified", "deleted"} else "modified"
            summary = ui_text(
                self._language,
                f"tool.card.change.{status_key}",
                path=path,
                additions=additions,
                deletions=deletions,
            )
            content_start = len(body) + 3
            self.append_tool_line(body, "├", summary, style="#8ed1e6")
            body.highlight_words((path,), style="bold #8ed1e6", case_sensitive=True)
            addition_text = f"+{additions}"
            addition_offset = summary.find(addition_text)
            if addition_offset >= 0:
                body.stylize(
                    "bold #9ce7b5 on #213a2b",
                    content_start + addition_offset,
                    content_start + addition_offset + len(addition_text),
                )
            deletion_text = f"-{deletions}"
            deletion_offset = summary.find(deletion_text)
            if deletion_offset >= 0:
                body.stylize(
                    "bold #ffb4ab on #4a221d",
                    content_start + deletion_offset,
                    content_start + deletion_offset + len(deletion_text),
                )

            raw_diff = change.get("diff")
            if expanded and isinstance(raw_diff, str) and raw_diff:
                diff_lines, _, omitted, truncated = self.bounded_tool_preview(raw_diff)
                for line in diff_lines:
                    body.append("\n│   ", style="#505860")
                    body.append(line, style=self.diff_line_style(line))
                if omitted or truncated or change.get("diff_truncated") is True:
                    body.append("\n│   ", style="#505860")
                    body.append(
                        ui_text(self._language, "tool.card.diff_truncated"),
                        style="italic #7f8790",
                    )
            hidden_reason = change.get("hidden_reason")
            if isinstance(hidden_reason, str):
                known_reason = (
                    hidden_reason
                    if hidden_reason in {"sensitive", "large", "budget", "binary", "redacted"}
                    else "unavailable"
                )
                body.append("\n│   ", style="#505860")
                body.append(
                    ui_text(self._language, f"tool.card.hidden.{known_reason}"),
                    style="italic #7f8790",
                )

        omitted_files = self.non_negative_int(report.get("omitted_files")) + max(
            0, len(files) - len(visible)
        )
        if omitted_files:
            self.append_tool_line(
                body,
                "├",
                ui_text(self._language, "tool.card.files_omitted", count=omitted_files),
                style="italic #7f8790",
            )
        if report.get("scan_limited") is True:
            self.append_tool_line(
                body,
                "├",
                ui_text(self._language, "tool.card.scan_limited"),
                style="italic #7f8790",
            )

    @staticmethod
    def non_negative_int(value: object) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    @staticmethod
    def diff_line_style(line: str) -> str:
        if line.startswith("@@"):
            return "bold #b6c2ff on #232637"
        if line.startswith(("+++", "---")):
            return "bold #7da7d9"
        if line.startswith("+"):
            return "#b7f7ca on #213a2b"
        if line.startswith("-"):
            return "#ffb4ab on #4a221d"
        return "#b8bcc2"
