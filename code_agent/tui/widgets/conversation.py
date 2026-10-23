from __future__ import annotations

from typing import ClassVar

from rich.console import RenderableType
from rich.markdown import Markdown
from textual import events
from textual.binding import Binding, BindingType
from textual.message import Message as TextualMessage
from textual.widgets import Static


class AssistantMarkdown(Markdown):
    """Safe model Markdown whose string form remains useful in diagnostics."""

    def __str__(self) -> str:
        return self.markup


class ConversationMessage(Static):
    """One stable message node in the scrollable conversation."""

    def __init__(
        self,
        category: str,
        rendered: RenderableType,
        *,
        pending: bool = False,
    ) -> None:
        classes = f"conversation-message message-{category}"
        if pending:
            classes += " message-pending"
        super().__init__(rendered, markup=False, classes=classes)
        self.category = category

    def set_pending(self, pending: bool) -> None:
        self.set_class(pending, "message-pending")


class ToolFeedbackMessage(ConversationMessage, can_focus=True):
    """A stable tool card whose already-bounded details can be toggled."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "toggle_details", "Toggle details", show=False),
        Binding("space", "toggle_details", "Toggle details", show=False),
    ]

    class ToggleRequested(TextualMessage):
        """Request that the owning app toggle one tool card."""

        def __init__(self, card: ToolFeedbackMessage) -> None:
            self.card = card
            super().__init__()

    def __init__(self, rendered: RenderableType, *, entry_index: int) -> None:
        super().__init__("tool", rendered)
        self.entry_index = entry_index

    async def _on_click(self, event: events.Click) -> None:
        event.stop()
        self.focus()
        self.post_message(self.ToggleRequested(self))

    def action_toggle_details(self) -> None:
        self.post_message(self.ToggleRequested(self))
