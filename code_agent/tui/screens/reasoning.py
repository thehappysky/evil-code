from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from code_agent.tui.domain import ReasoningEffort, UiLanguage
from code_agent.tui.text import ui_text


_EFFORT_COLORS = {
    ReasoningEffort.LOW: "#8e939a",
    ReasoningEffort.MEDIUM: "#6fb7d6",
    ReasoningEffort.HIGH: "#78c2a4",
    ReasoningEffort.XHIGH: "#a9a1e8",
    ReasoningEffort.ULTRACODE: "#d58cc8",
}


class ReasoningEffortScreen(ModalScreen[ReasoningEffort | None]):
    """Select application-owned review depth without implying native API support."""

    CSS = """
    ReasoningEffortScreen {
        align: center middle;
        background: $background 70%;
    }

    #effort-dialog {
        width: 90%;
        max-width: 100;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: heavy $primary;
        background: $surface;
    }

    #effort-title {
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }

    #effort-options {
        height: auto;
        max-height: 20;
    }

    #effort-options Button {
        width: 100%;
        margin-bottom: 1;
        content-align: left middle;
    }

    #effort-help {
        color: $text-muted;
        margin-top: 1;
    }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("ctrl+c", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        selected: ReasoningEffort,
        *,
        language: UiLanguage = UiLanguage.SIMPLIFIED_CHINESE,
    ) -> None:
        super().__init__()
        self.selected = selected
        self.language = language
        self._choice_ids = {
            f"effort-choice-{index}": effort for index, effort in enumerate(ReasoningEffort)
        }

    def _label(self, effort: ReasoningEffort) -> Text:
        color = _EFFORT_COLORS[effort]
        rendered = Text(f"{effort.glyph}  {effort.value}", style=f"bold {color}")
        rendered.append(
            f"  ·  {ui_text(self.language, f'effort.description.{effort.value}')}",
            style="#b0b4ba",
        )
        if effort is self.selected:
            rendered.append(
                f"  ({ui_text(self.language, 'marker.current')})",
                style="bold #8b9cff",
            )
        if effort is ReasoningEffort.ULTRACODE:
            rendered.append(
                f"  ({ui_text(self.language, 'effort.workflow_planned')})",
                style="#d58cc8",
            )
        return rendered

    def compose(self) -> ComposeResult:
        buttons = [
            Button(
                self._label(effort),
                id=f"effort-choice-{index}",
                variant="primary" if effort is self.selected else "default",
            )
            for index, effort in enumerate(ReasoningEffort)
        ]
        yield Vertical(
            Label(ui_text(self.language, "effort.title"), id="effort-title"),
            VerticalScroll(*buttons, id="effort-options"),
            Static(ui_text(self.language, "effort.help"), id="effort-help"),
            id="effort-dialog",
        )

    def on_mount(self) -> None:
        index = tuple(ReasoningEffort).index(self.selected)
        self.query_one(f"#effort-choice-{index}", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        effort = self._choice_ids.get(event.button.id or "")
        if effort is not None:
            self.dismiss(effort)

    def action_cancel(self) -> None:
        self.dismiss(None)
