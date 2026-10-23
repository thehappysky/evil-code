from __future__ import annotations

import os
from typing import Any, ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import Button, Input, Label, Static

from code_agent.tui.domain import (
    HttpClientPolicy,
    ManagedProviderProfile,
    ManagedProviderSettings,
    ProviderCatalog,
    ProviderCatalogError,
    ProviderCatalogResult,
    ProviderConnectionSpec,
    ProviderOption,
    ProviderSettingsStore,
    ProviderSettingsSubmission,
    UiLanguage,
    _PROVIDER_PRESETS,
    _is_deepseek_base_url,
    redact_sensitive_text,
    resolve_http_client_policy,
)
from code_agent.tui.text import ui_text


_EVIL_CODE_THEME = Theme(
    name="evil-code-dark",
    primary="#8b9cff",
    secondary="#777c83",
    accent="#6fc3df",
    warning="#e59c74",
    error="#c76d6d",
    success="#78c2a4",
    foreground="#d8dadd",
    background="#101214",
    surface="#1a1c1f",
    panel="#16181b",
    boost="#2a2d31",
    luminosity_spread=0.08,
    text_alpha=0.96,
    variables={
        "border": "#5866a3",
        "border-blurred": "#3b3f44",
        "block-cursor-background": "#8b9cff",
        "block-cursor-foreground": "#101214",
        "block-hover-background": "#2a2d31",
        "button-color-foreground": "#101214",
        "button-focus-text-style": "bold",
        "footer-background": "#141619",
        "footer-description-background": "#141619",
        "footer-description-foreground": "#a9adb3",
        "footer-item-background": "#141619",
        "footer-key-background": "#141619",
        "footer-key-foreground": "#8b9cff",
        "input-cursor-background": "#d8dadd",
        "input-cursor-foreground": "#101214",
        "input-selection-background": "#5866a3 55%",
        "scrollbar": "#50555b",
        "scrollbar-active": "#6878ba",
        "scrollbar-background": "#101214",
        "scrollbar-hover": "#666b72",
    },
)


class ProviderSettingsScreen(ModalScreen[ProviderSettingsSubmission | None]):
    """Create and edit user-owned provider profiles on a focused detail screen."""

    CSS = """
    ProviderSettingsScreen {
        align: center middle;
        background: $background 70%;
    }

    #provider-settings-dialog {
        width: 90%;
        max-width: 105;
        height: 95%;
        max-height: 95%;
        padding: 1 2;
        border: heavy $primary;
        background: $surface;
    }

    #provider-settings-content {
        height: 1fr;
    }

    #provider-settings-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    #provider-settings-description,
    #provider-settings-protocol-hint,
    #provider-settings-proxy-title,
    #provider-settings-proxy-hint,
    #provider-settings-connection-status,
    #provider-settings-error,
    #provider-settings-empty {
        color: $text-muted;
        margin-bottom: 1;
    }

    #provider-settings-protocol-hint {
        color: #9cc4cc;
    }

    #provider-settings-proxy-title {
        color: $text;
        text-style: bold;
        margin-top: 1;
    }

    #provider-settings-proxy-hint {
        color: #9cc4cc;
    }

    #provider-settings-connection-status {
        margin-top: 1;
        margin-bottom: 1;
    }

    #provider-settings-error {
        color: $error;
    }

    #provider-settings-profiles {
        height: auto;
        max-height: 8;
        margin-bottom: 1;
    }

    #provider-settings-models {
        display: none;
        height: auto;
        max-height: 10;
        margin-bottom: 1;
    }

    #provider-settings-models Button {
        width: 100%;
        margin-bottom: 1;
    }

    #provider-settings-profiles Button {
        width: 100%;
        margin-bottom: 1;
    }

    #provider-settings-presets,
    #provider-settings-presets-row-one,
    #provider-settings-presets-row-two,
    #provider-settings-proxy-modes,
    #provider-settings-form,
    #provider-settings-actions {
        height: auto;
    }

    #provider-settings-presets {
        margin-bottom: 1;
    }

    #provider-settings-presets Button {
        width: 1fr;
        margin-right: 1;
    }

    #provider-settings-presets-row-one {
        margin-bottom: 1;
    }

    #provider-settings-form Input {
        margin-bottom: 0;
    }

    #provider-settings-proxy-modes Button {
        width: 1fr;
        margin-right: 1;
    }

    #provider-settings-form {
        margin-bottom: 1;
    }

    #provider-settings-actions {
        align-horizontal: right;
    }

    #provider-settings-actions Button {
        margin-left: 1;
    }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Back", show=False),
        Binding("ctrl+c", "cancel", "Back", show=False),
    ]

    def __init__(
        self,
        *,
        language: UiLanguage,
        provider_settings: ManagedProviderSettings,
        provider_settings_store: ProviderSettingsStore,
        provider_catalog: ProviderCatalog | None = None,
        first_run: bool = False,
        initial_profile: str | None = None,
        initial_error: str | None = None,
    ) -> None:
        super().__init__()
        self.language = language
        self.provider_settings = provider_settings
        self.provider_settings_store = provider_settings_store
        self.provider_catalog = provider_catalog
        self.first_run = first_run
        self.initial_profile = initial_profile
        self.initial_error = initial_error
        self._editing_profile: str | None = None
        self._active_preset = "openai"
        self._active_proxy_mode = "environment"
        self._delete_confirmation_for: str | None = None
        self._catalog_model_ids: dict[str, str] = {}
        self._profile_ids = {
            f"provider-settings-profile-{index}": profile.name
            for index, profile in enumerate(provider_settings.profiles)
        }

    def compose(self) -> ComposeResult:
        profile_widgets: list[Any] = [
            Button(
                self._provider_label(profile),
                id=f"provider-settings-profile-{index}",
                variant=(
                    "primary"
                    if profile.name == self.provider_settings.default_provider
                    else "default"
                ),
            )
            for index, profile in enumerate(self.provider_settings.profiles)
        ]
        if not profile_widgets:
            profile_widgets.append(
                Static(
                    ui_text(self.language, "provider_settings.empty"), id="provider-settings-empty"
                )
            )
        preset_buttons = [
            Button(
                ui_text(self.language, f"provider_settings.preset.{preset.name}"),
                id=f"provider-settings-preset-{preset.name}",
                variant="primary" if preset.name == self._active_preset else "default",
            )
            for preset in _PROVIDER_PRESETS
        ]
        actions: list[Any] = []
        if not self.first_run:
            actions.extend(
                (
                    Button(ui_text(self.language, "settings.back"), id="provider-settings-back"),
                    Button(
                        ui_text(self.language, "provider_settings.delete"),
                        id="provider-settings-delete",
                        disabled=True,
                    ),
                )
            )
        actions.extend(
            (
                Button(
                    ui_text(self.language, "provider_settings.new"),
                    id="provider-settings-new",
                ),
                Button(
                    ui_text(self.language, "provider_settings.connection.test"),
                    id="provider-settings-test",
                    disabled=self.provider_catalog is None,
                ),
                Button(
                    ui_text(self.language, "provider_settings.save_use"),
                    id="provider-settings-save",
                    variant="success",
                ),
            )
        )
        yield Vertical(
            VerticalScroll(
                Label(
                    ui_text(
                        self.language,
                        "provider_settings.first_run_title"
                        if self.first_run
                        else "provider_settings.title",
                    ),
                    id="provider-settings-title",
                ),
                Static(
                    ui_text(self.language, "provider_settings.description"),
                    id="provider-settings-description",
                ),
                VerticalScroll(*profile_widgets, id="provider-settings-profiles"),
                Vertical(
                    Horizontal(*preset_buttons[:3], id="provider-settings-presets-row-one"),
                    Horizontal(*preset_buttons[3:], id="provider-settings-presets-row-two"),
                    id="provider-settings-presets",
                ),
                Static(
                    ui_text(self.language, "provider_settings.protocol.openai"),
                    id="provider-settings-protocol-hint",
                ),
                Vertical(
                    Input(
                        placeholder=ui_text(self.language, "provider_settings.name"),
                        id="provider-settings-name",
                    ),
                    Input(
                        placeholder=ui_text(self.language, "provider_settings.model.openai"),
                        id="provider-settings-model",
                    ),
                    Input(
                        value="https://api.openai.com/v1",
                        placeholder=ui_text(self.language, "provider_settings.base_url"),
                        id="provider-settings-base-url",
                    ),
                    Input(
                        placeholder=ui_text(self.language, "provider_settings.api_key"),
                        password=True,
                        id="provider-settings-api-key",
                    ),
                    Static(
                        ui_text(self.language, "provider_settings.proxy.title"),
                        id="provider-settings-proxy-title",
                    ),
                    Horizontal(
                        Button(
                            ui_text(self.language, "provider_settings.proxy.environment"),
                            id="provider-settings-proxy-environment",
                            variant="primary",
                        ),
                        Button(
                            ui_text(self.language, "provider_settings.proxy.direct"),
                            id="provider-settings-proxy-direct",
                        ),
                        Button(
                            ui_text(self.language, "provider_settings.proxy.explicit"),
                            id="provider-settings-proxy-explicit",
                        ),
                        id="provider-settings-proxy-modes",
                    ),
                    Input(
                        placeholder=ui_text(
                            self.language,
                            "provider_settings.proxy.environment_variable",
                        ),
                        id="provider-settings-proxy-env",
                        disabled=True,
                    ),
                    Static(
                        ui_text(
                            self.language,
                            "provider_settings.proxy.hint.environment",
                        ),
                        id="provider-settings-proxy-hint",
                    ),
                    Static("", id="provider-settings-connection-status"),
                    VerticalScroll(id="provider-settings-models"),
                    id="provider-settings-form",
                ),
                Static("", id="provider-settings-error"),
                id="provider-settings-content",
            ),
            Horizontal(*actions, id="provider-settings-actions"),
            id="provider-settings-dialog",
        )

    def _provider_label(self, profile: ManagedProviderProfile) -> str:
        suffix = (
            f" · {ui_text(self.language, 'marker.default')}"
            if profile.name == self.provider_settings.default_provider
            else ""
        )
        return f"{profile.name} · {profile.model}{suffix}"

    def on_mount(self) -> None:
        if self.initial_profile is not None:
            self._edit_profile(self.initial_profile)
        if self.initial_error:
            self._show_provider_error(self.initial_error)
        focus_target = (
            "#provider-settings-model"
            if self._editing_profile is not None
            else "#provider-settings-name"
        )
        self.query_one(focus_target, Input).focus()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        profile_name = self._profile_ids.get(button_id)
        if profile_name is not None:
            self._edit_profile(profile_name)
            return
        catalog_model = self._catalog_model_ids.get(button_id)
        if catalog_model is not None:
            self.query_one("#provider-settings-model", Input).value = catalog_model
            self._show_connection_status(
                ui_text(
                    self.language,
                    "provider_settings.connection.selected",
                    model=catalog_model,
                ),
                kind="success",
            )
            return
        if button_id.startswith("provider-settings-preset-"):
            self._select_preset(button_id.removeprefix("provider-settings-preset-"))
            return
        if button_id.startswith("provider-settings-proxy-"):
            self._select_proxy_mode(button_id.removeprefix("provider-settings-proxy-"))
            return
        if button_id == "provider-settings-new":
            self._new_profile()
            return
        if button_id == "provider-settings-save":
            await self._save_provider()
            return
        if button_id == "provider-settings-test":
            self.run_worker(
                self._test_connection(),
                name="provider-model-discovery",
                group="provider-model-discovery",
                exclusive=True,
                exit_on_error=False,
            )
            return
        if button_id == "provider-settings-delete":
            await self._delete_provider()
            return
        if button_id == "provider-settings-back":
            self.dismiss(None)

    def _edit_profile(self, name: str) -> None:
        profile = self.provider_settings.profile(name)
        if profile is None:
            return
        self._editing_profile = name
        self._clear_model_catalog()
        name_input = self.query_one("#provider-settings-name", Input)
        name_input.value = profile.name
        name_input.disabled = True
        self.query_one("#provider-settings-model", Input).value = profile.model
        self.query_one("#provider-settings-base-url", Input).value = profile.base_url
        self.query_one("#provider-settings-api-key", Input).value = ""
        self._select_preset(self._preset_for_profile(profile), update_endpoint=False)
        self.query_one("#provider-settings-proxy-env", Input).value = profile.proxy_url_env or ""
        self._select_proxy_mode(profile.proxy_mode)
        self._reset_delete_confirmation()
        if not self.first_run:
            self.query_one("#provider-settings-delete", Button).disabled = False
        self._show_provider_error("")
        self.query_one("#provider-settings-model", Input).focus()

    @staticmethod
    def _preset_for_profile(profile: ManagedProviderProfile) -> str:
        base_url = profile.base_url.rstrip("/").casefold()
        if _is_deepseek_base_url(profile.base_url):
            return "deepseek"
        exact = next(
            (
                preset.name
                for preset in _PROVIDER_PRESETS
                if preset.base_url
                and base_url == preset.base_url.rstrip("/").casefold()
                and preset.protocol == profile.protocol
                and preset.dialect == profile.dialect
            ),
            None,
        )
        if exact is not None:
            return exact
        return next(
            (
                preset.name
                for preset in _PROVIDER_PRESETS
                if preset.protocol == profile.protocol and preset.dialect == profile.dialect
            ),
            "compatible",
        )

    def _new_profile(self) -> None:
        self._editing_profile = None
        self._clear_model_catalog()
        name_input = self.query_one("#provider-settings-name", Input)
        name_input.disabled = False
        name_input.value = ""
        self.query_one("#provider-settings-model", Input).value = ""
        self.query_one("#provider-settings-api-key", Input).value = ""
        self.query_one("#provider-settings-proxy-env", Input).value = ""
        self._select_preset("openai")
        self._select_proxy_mode("environment")
        self._reset_delete_confirmation()
        if not self.first_run:
            self.query_one("#provider-settings-delete", Button).disabled = True
        self._show_provider_error("")
        name_input.focus()

    def _select_preset(self, preset_name: str, *, update_endpoint: bool = True) -> None:
        preset = next(
            (entry for entry in _PROVIDER_PRESETS if entry.name == preset_name),
            None,
        )
        if preset is None:
            return
        self._clear_model_catalog()
        self._active_preset = preset_name
        for candidate in _PROVIDER_PRESETS:
            button = self.query_one(f"#provider-settings-preset-{candidate.name}", Button)
            button.variant = "primary" if candidate.name == preset_name else "default"
        self.query_one("#provider-settings-protocol-hint", Static).update(
            ui_text(self.language, f"provider_settings.protocol.{preset_name}")
        )
        self.query_one("#provider-settings-model", Input).placeholder = ui_text(
            self.language,
            f"provider_settings.model.{preset_name}",
        )
        if update_endpoint:
            self.query_one("#provider-settings-base-url", Input).value = preset.base_url

    def _select_proxy_mode(self, proxy_mode: str) -> None:
        if proxy_mode not in {"environment", "direct", "explicit"}:
            return
        self._clear_model_catalog()
        self._active_proxy_mode = proxy_mode
        for candidate in ("environment", "direct", "explicit"):
            button = self.query_one(f"#provider-settings-proxy-{candidate}", Button)
            button.variant = "primary" if candidate == proxy_mode else "default"
        proxy_env = self.query_one("#provider-settings-proxy-env", Input)
        proxy_env.disabled = proxy_mode != "explicit"
        self.query_one("#provider-settings-proxy-hint", Static).update(
            ui_text(self.language, f"provider_settings.proxy.hint.{proxy_mode}")
        )
        self._reset_delete_confirmation()

    def _connection_spec(self) -> tuple[ProviderConnectionSpec, HttpClientPolicy]:
        preset = next(entry for entry in _PROVIDER_PRESETS if entry.name == self._active_preset)
        base_url = self.query_one("#provider-settings-base-url", Input).value.strip()
        if _is_deepseek_base_url(base_url) and preset.protocol != "openai-chat":
            raise ValueError(ui_text(self.language, "provider_settings.deepseek_protocol_required"))
        name = self.query_one("#provider-settings-name", Input).value.strip()
        existing = self.provider_settings.profile(name)
        entered_api_key = self.query_one("#provider-settings-api-key", Input).value.strip()
        api_key = entered_api_key or (existing.api_key if existing is not None else None)
        if api_key is None:
            raise ValueError(ui_text(self.language, "provider_settings.api_key_required"))
        proxy_url_env = (
            self.query_one("#provider-settings-proxy-env", Input).value.strip() or None
            if self._active_proxy_mode == "explicit"
            else None
        )
        policy = resolve_http_client_policy(
            proxy_mode=self._active_proxy_mode,
            proxy_url_env=proxy_url_env,
            environ=os.environ,
        )
        return (
            ProviderConnectionSpec(
                protocol=preset.protocol,
                dialect=preset.dialect,
                base_url=base_url,
                api_key=api_key,
            ),
            policy,
        )

    async def _test_connection(self) -> None:
        if self.provider_catalog is None:
            return
        button = self.query_one("#provider-settings-test", Button)
        button.disabled = True
        button.label = ui_text(self.language, "provider_settings.connection.testing")
        self._clear_model_catalog()
        self._show_provider_error("")
        self._show_connection_status(
            ui_text(self.language, "provider_settings.connection.testing"),
            kind="normal",
        )
        signature: tuple[str, ...] | None = None
        spec: ProviderConnectionSpec | None = None
        try:
            spec, policy = self._connection_spec()
            signature = self._connection_signature()
            result = await self.provider_catalog.discover_models(spec, http_policy=policy)
            if signature != self._connection_signature():
                self._show_connection_status(
                    ui_text(self.language, "provider_settings.connection.stale"),
                    kind="warning",
                )
                return
            await self._show_model_catalog(result)
        except Exception as error:
            if signature is not None and signature != self._connection_signature():
                self._show_connection_status(
                    ui_text(self.language, "provider_settings.connection.stale"),
                    kind="warning",
                )
            else:
                self._show_connection_status(
                    self._connection_error_message(
                        error,
                        api_key=spec.api_key if spec is not None else None,
                    ),
                    kind="error",
                )
        finally:
            button.disabled = False
            button.label = ui_text(self.language, "provider_settings.connection.test")

    def _connection_signature(self) -> tuple[str, ...]:
        return (
            self._active_preset,
            self._active_proxy_mode,
            self.query_one("#provider-settings-name", Input).value,
            self.query_one("#provider-settings-base-url", Input).value,
            self.query_one("#provider-settings-api-key", Input).value,
            self.query_one("#provider-settings-proxy-env", Input).value,
        )

    async def _show_model_catalog(self, result: ProviderCatalogResult) -> None:
        container = self.query_one("#provider-settings-models", VerticalScroll)
        await container.remove_children()
        self._catalog_model_ids = {
            f"provider-settings-catalog-model-{index}": model
            for index, model in enumerate(result.models)
        }
        if result.models:
            await container.mount(
                *(
                    Button(Text(model), id=button_id)
                    for button_id, model in self._catalog_model_ids.items()
                )
            )
            container.display = True
        else:
            container.display = False
        selected_model = self.query_one("#provider-settings-model", Input).value.strip()
        if not result.models:
            message = ui_text(self.language, "provider_settings.connection.success_empty")
            kind = "success"
        elif selected_model and selected_model in result.models:
            message = ui_text(
                self.language,
                "provider_settings.connection.success_selected",
                count=len(result.models),
                model=selected_model,
            )
            kind = "success"
        elif selected_model:
            message = ui_text(
                self.language,
                "provider_settings.connection.success_missing",
                count=len(result.models),
                model=selected_model,
            )
            kind = "warning"
        else:
            message = ui_text(
                self.language,
                "provider_settings.connection.success",
                count=len(result.models),
            )
            kind = "success"
        if result.truncated:
            message += ui_text(self.language, "provider_settings.connection.truncated")
        self._show_connection_status(message, kind=kind)

    def _connection_error_message(self, error: Exception, *, api_key: str | None = None) -> str:
        if isinstance(error, ProviderCatalogError):
            key = {
                "authentication": "authentication",
                "endpoint": "endpoint",
                "timeout": "timeout",
                "rate_limit": "rate_limit",
                "server": "server",
                "http": "http",
                "proxy": "proxy",
                "network": "network",
                "response_too_large": "response_too_large",
                "invalid_response": "invalid_response",
            }.get(error.kind, "unknown")
            return ui_text(
                self.language,
                f"provider_settings.connection.error.{key}",
                status=error.status_code if error.status_code is not None else "?",
                detail=error.detail or ui_text(self.language, "value.unknown"),
            )
        entered_api_key = self.query_one("#provider-settings-api-key", Input).value.strip()
        return redact_sensitive_text(str(error), explicit_values=(entered_api_key, api_key or ""))

    def _clear_model_catalog(self) -> None:
        self._catalog_model_ids = {}
        if self.is_mounted:
            self.query_one("#provider-settings-models", VerticalScroll).display = False
            self.query_one("#provider-settings-connection-status", Static).update("")

    def _show_connection_status(self, message: str, *, kind: str) -> None:
        color = {
            "success": "#78c2a4",
            "warning": "#e59c74",
            "error": "#c76d6d",
        }.get(kind, "#9cc4cc")
        self.query_one("#provider-settings-connection-status", Static).update(
            Text(message, style=color)
        )

    async def _save_provider(self) -> None:
        preset = next(entry for entry in _PROVIDER_PRESETS if entry.name == self._active_preset)
        api_key = self.query_one("#provider-settings-api-key", Input).value.strip() or None
        name = self.query_one("#provider-settings-name", Input).value.strip()
        base_url = self.query_one("#provider-settings-base-url", Input).value.strip()
        proxy_url_env = (
            self.query_one("#provider-settings-proxy-env", Input).value.strip() or None
            if self._active_proxy_mode == "explicit"
            else None
        )
        try:
            if _is_deepseek_base_url(base_url) and preset.protocol != "openai-chat":
                raise ValueError(
                    ui_text(self.language, "provider_settings.deepseek_protocol_required")
                )
            profile = ManagedProviderProfile(
                name=name,
                protocol=preset.protocol,
                dialect=preset.dialect,
                model=self.query_one("#provider-settings-model", Input).value.strip(),
                base_url=base_url,
                proxy_mode=self._active_proxy_mode,
                proxy_url_env=proxy_url_env,
                api_key=api_key,
            )
            existing = self.provider_settings.profile(name)
            if existing is None and api_key is None:
                raise ValueError(ui_text(self.language, "provider_settings.api_key_required"))
            resolve_http_client_policy(
                proxy_mode=profile.proxy_mode,
                proxy_url_env=profile.proxy_url_env,
                environ=os.environ,
            )
            await self.provider_settings_store.save_profile(profile, make_default=True)
        except Exception as error:
            self._show_provider_error(str(error))
            return
        self.dismiss(ProviderSettingsSubmission(profile.name))

    async def _delete_provider(self) -> None:
        profile_name = self._editing_profile
        if profile_name is None:
            return
        if self._delete_confirmation_for != profile_name:
            self._delete_confirmation_for = profile_name
            button = self.query_one("#provider-settings-delete", Button)
            button.label = ui_text(self.language, "provider_settings.delete_confirm")
            button.variant = "error"
            self._show_provider_error(
                ui_text(
                    self.language,
                    "provider_settings.delete_warning",
                    profile=profile_name,
                )
            )
            return
        try:
            await self.provider_settings_store.delete_profile(profile_name)
        except Exception as error:
            self._show_provider_error(str(error))
            return
        self.dismiss(ProviderSettingsSubmission(profile_name, operation="deleted"))

    def _reset_delete_confirmation(self) -> None:
        self._delete_confirmation_for = None
        if not self.first_run and self.is_mounted:
            button = self.query_one("#provider-settings-delete", Button)
            button.label = ui_text(self.language, "provider_settings.delete")
            button.variant = "default"

    def _show_provider_error(self, message: str) -> None:
        self.query_one("#provider-settings-error", Static).update(message)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ProviderSetupApp(App[bool]):
    """Focused provider setup used for first run and recoverable startup errors."""

    CSS = "Screen { background: $background; }"

    def __init__(
        self,
        *,
        provider_settings: ManagedProviderSettings,
        provider_settings_store: ProviderSettingsStore,
        provider_catalog: ProviderCatalog | None = None,
        language: UiLanguage = UiLanguage.SIMPLIFIED_CHINESE,
        first_run: bool = True,
        initial_profile: str | None = None,
        initial_error: str | None = None,
    ) -> None:
        super().__init__()
        self.register_theme(_EVIL_CODE_THEME)
        self.theme = _EVIL_CODE_THEME.name
        self._provider_settings = provider_settings
        self._provider_settings_store = provider_settings_store
        self._provider_catalog = provider_catalog
        self._language = language
        self._first_run = first_run
        self._initial_profile = initial_profile
        self._initial_error = initial_error

    def on_mount(self) -> None:
        self.push_screen(
            ProviderSettingsScreen(
                language=self._language,
                provider_settings=self._provider_settings,
                provider_settings_store=self._provider_settings_store,
                provider_catalog=self._provider_catalog,
                first_run=self._first_run,
                initial_profile=self._initial_profile,
                initial_error=self._initial_error,
            ),
            self._setup_finished,
        )

    def _setup_finished(
        self,
        result: ProviderSettingsSubmission | None,
    ) -> None:
        self.exit(isinstance(result, ProviderSettingsSubmission))


class ProviderSelectionScreen(ModalScreen[str | None]):
    """Select one configured profile without exposing credentials or endpoints."""

    CSS = """
    ProviderSelectionScreen {
        align: center middle;
        background: $background 70%;
    }

    #provider-dialog {
        width: 90%;
        max-width: 110;
        height: 80%;
        padding: 1 2;
        border: heavy $primary;
        background: $surface;
    }

    #provider-title {
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }

    #provider-options {
        height: 1fr;
    }

    #provider-options Button {
        width: 100%;
        margin-bottom: 1;
    }

    #provider-help {
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
        options: tuple[ProviderOption, ...],
        *,
        language: UiLanguage = UiLanguage.SIMPLIFIED_CHINESE,
    ) -> None:
        super().__init__()
        self.options = options
        self.language = language
        self._choice_ids = {
            f"provider-choice-{index}": option.name for index, option in enumerate(options)
        }

    @staticmethod
    def _label(
        option: ProviderOption,
        language: UiLanguage = UiLanguage.SIMPLIFIED_CHINESE,
    ) -> str:
        markers: list[str] = []
        if option.selected:
            markers.append(ui_text(language, "marker.current"))
        if option.default:
            markers.append(ui_text(language, "marker.default"))
        if not option.available:
            markers.append(ui_text(language, "marker.unavailable"))
        elif not option.credential_configured:
            markers.append(ui_text(language, "marker.credential_missing"))
        suffix = f" ({' · '.join(markers)})" if markers else ""
        return f"{option.name} · {option.model} · {option.protocol}{suffix}"

    def compose(self) -> ComposeResult:
        buttons = [
            Button(
                Text(self._label(option, self.language)),
                id=f"provider-choice-{index}",
                variant="primary" if option.selected else "default",
                disabled=not option.selectable,
            )
            for index, option in enumerate(self.options)
        ]
        yield Vertical(
            Label(ui_text(self.language, "provider.title"), id="provider-title"),
            VerticalScroll(*buttons, id="provider-options"),
            Static(
                ui_text(self.language, "provider.help"),
                id="provider-help",
            ),
            id="provider-dialog",
        )

    def on_mount(self) -> None:
        target: Button | None = None
        for index, option in enumerate(self.options):
            button = self.query_one(f"#provider-choice-{index}", Button)
            if option.selected and not button.disabled:
                target = button
                break
            if target is None and not button.disabled:
                target = button
        if target is not None:
            target.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        profile_name = self._choice_ids.get(event.button.id or "")
        if profile_name is not None:
            self.dismiss(profile_name)

    def action_cancel(self) -> None:
        self.dismiss(None)
