from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from code_agent.tui.domain import UiLanguage
from code_agent.tui.text import language_name, ui_text


class SettingsScreen(ModalScreen[str | None]):
    """First-level settings navigation; detailed forms live on child screens."""

    CSS = """
    SettingsScreen {
        align: center middle;
        background: $background 70%;
    }

    #settings-dialog {
        width: 82%;
        max-width: 78;
        height: auto;
        max-height: 85%;
        padding: 1 2;
        border: heavy $primary;
        background: $surface;
    }

    #settings-title {
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }

    #settings-description {
        color: $text-muted;
        margin-bottom: 1;
    }

    #settings-categories {
        height: auto;
    }

    #settings-categories Button {
        width: 100%;
        margin-bottom: 1;
        content-align: left middle;
    }

    #settings-help {
        color: $text-muted;
    }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("ctrl+c", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        selected: UiLanguage,
        *,
        language: UiLanguage,
        provider_settings_available: bool,
    ) -> None:
        super().__init__()
        self.selected = selected
        self.language = language
        self.provider_settings_available = provider_settings_available

    def compose(self) -> ComposeResult:
        language_summary = language_name(self.selected, in_language=self.language)
        yield Vertical(
            Label(ui_text(self.language, "settings.title"), id="settings-title"),
            Static(ui_text(self.language, "settings.description"), id="settings-description"),
            Vertical(
                Button(
                    ui_text(
                        self.language,
                        "settings.category.language",
                        current=language_summary,
                    ),
                    id="settings-category-language",
                    variant="primary",
                ),
                Button(
                    ui_text(self.language, "settings.category.providers"),
                    id="settings-category-providers",
                    disabled=not self.provider_settings_available,
                ),
                id="settings-categories",
            ),
            Static(ui_text(self.language, "settings.help"), id="settings-help"),
            id="settings-dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#settings-category-language", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        categories = {
            "settings-category-language": "language",
            "settings-category-providers": "providers",
        }
        category = categories.get(event.button.id or "")
        if category is not None:
            self.dismiss(category)

    def action_cancel(self) -> None:
        self.dismiss(None)


class LanguageSettingsScreen(ModalScreen[UiLanguage | None]):
    """Edit one interface preference without rendering unrelated provider fields."""

    CSS = """
    LanguageSettingsScreen {
        align: center middle;
        background: $background 70%;
    }

    #language-settings-dialog {
        width: 82%;
        max-width: 78;
        height: auto;
        padding: 1 2;
        border: heavy $primary;
        background: $surface;
    }

    #language-settings-title {
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }

    #language-settings-description,
    #language-settings-help {
        color: $text-muted;
        margin-bottom: 1;
    }

    #settings-languages,
    #language-settings-actions {
        height: auto;
    }

    #settings-languages Button {
        width: 1fr;
        margin-right: 1;
    }

    #language-settings-actions {
        align-horizontal: right;
    }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Back", show=False),
        Binding("ctrl+c", "cancel", "Back", show=False),
    ]

    def __init__(self, selected: UiLanguage, *, language: UiLanguage) -> None:
        super().__init__()
        self.selected = selected
        self.language = language

    def _choice_label(self, choice: UiLanguage) -> str:
        label = language_name(choice, in_language=choice)
        if choice is self.selected:
            label += f" · {ui_text(self.language, 'settings.current')}"
        return label

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(
                ui_text(self.language, "settings.language.title"),
                id="language-settings-title",
            ),
            Static(
                ui_text(self.language, "settings.language.description"),
                id="language-settings-description",
            ),
            Horizontal(
                Button(
                    self._choice_label(UiLanguage.SIMPLIFIED_CHINESE),
                    id="settings-language-zh-cn",
                    variant=(
                        "primary" if self.selected is UiLanguage.SIMPLIFIED_CHINESE else "default"
                    ),
                ),
                Button(
                    self._choice_label(UiLanguage.ENGLISH),
                    id="settings-language-en",
                    variant="primary" if self.selected is UiLanguage.ENGLISH else "default",
                ),
                id="settings-languages",
            ),
            Static(
                ui_text(self.language, "settings.language.help"),
                id="language-settings-help",
            ),
            Horizontal(
                Button(ui_text(self.language, "settings.back"), id="language-settings-back"),
                id="language-settings-actions",
            ),
            id="language-settings-dialog",
        )

    def on_mount(self) -> None:
        selector = (
            "#settings-language-zh-cn"
            if self.selected is UiLanguage.SIMPLIFIED_CHINESE
            else "#settings-language-en"
        )
        self.query_one(selector, Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        choices = {
            "settings-language-zh-cn": UiLanguage.SIMPLIFIED_CHINESE,
            "settings-language-en": UiLanguage.ENGLISH,
        }
        choice = choices.get(event.button.id or "")
        if choice is not None:
            self.dismiss(choice)
        elif event.button.id == "language-settings-back":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
