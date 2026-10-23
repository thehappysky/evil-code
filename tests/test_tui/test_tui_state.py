# [TUI] 2026-08-02: Headless state tests for code_agent/tui/tui.py.
"""Headless Textual state tests for ``code_agent/tui/tui.py``.

These tests exercise the UI *state transitions* of the Evil Code TUI without a
real agent or any network I/O. They follow the ``FakeRunner`` + ``run_test()``
pilot pattern from ``test_app.py`` and read private app state (``app._entries``,
``app._interaction_mode``, ...) exactly the way the existing TUI tests do.

Covered:
- Interaction-mode cycling via ``action_cycle_interaction_mode`` / the
  ``shift+tab`` binding and ``_apply_interaction_mode`` (runtime bar + status)
- Language switching and preference persistence
- Provider-selection application through ``_apply_provider_selection``
- The slash-command dispatch table in ``_dispatch_slash_command``
- The ``TUI_RELOAD_PROVIDER_SETTINGS`` save-and-reload contract
- Pure helpers: ``_bounded_tool_preview``, ``_event_duration``,
  ``_tool_change_report``, ``_context_percentage``/``_context_color``,
  ``_semantic_value_style``, ``_display_cwd``, ``CollapsingPulseAnimation``
- ``ProviderSettingsScreen`` create/edit/delete flows against an in-memory
  ``ProviderSettingsStore`` stub (no network)
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Button, Input, Static

from code_agent.tui.domain import (
    AgentEvent,
    AgentEventKind,
    AgentRunResult,
    InteractionMode,
    InteractionModeSelectionResult,
    ManagedProviderProfile,
    ManagedProviderSettings,
    ProviderOption,
    ProviderSelectionResult,
    ProviderSettingsSubmission,
    ReasoningEffort,
    ReasoningEffortSelectionResult,
    ToolFeedbackState,
    UiLanguage,
)
from code_agent.tui.stores import JsonUiPreferencesStore
from code_agent.tui.text import ui_text
from code_agent.tui.tui import (
    TUI_RELOAD_PROVIDER_SETTINGS,
    CollapsingPulseAnimation,
    EvilCodeApp,
    ProviderSettingsScreen,
    SettingsScreen,
)
from code_agent.tui.rendering import ToolFeedbackRenderer


# ─── Test doubles ──────────────────────────────────────────────────────────────


class FakeRunner:
    """ConversationRunner that streams one canned response through the sink."""

    session_id: str | None = None

    async def run(self, prompt: str, *, sink=None) -> AgentRunResult:
        if sink is not None:
            for fragment in ("hello", " from agent"):
                outcome = sink(
                    AgentEvent.create(1, AgentEventKind.TEXT_DELTA, {"text": fragment})
                )
                if inspect.isawaitable(outcome):
                    await outcome
        return AgentRunResult(None, "hello from agent", (), (), (), 1)


class StubInteractionModeController:
    """InteractionModeController protocol stub driven entirely in-memory."""

    def __init__(
        self,
        mode: InteractionMode = InteractionMode.NORMAL,
        auto_unrestricted: bool = False,
    ) -> None:
        self.interaction_mode = mode
        self.auto_mode_unrestricted = auto_unrestricted
        self.calls: list[InteractionMode] = []

    async def set_interaction_mode(
        self, mode: InteractionMode
    ) -> InteractionModeSelectionResult:
        self.calls.append(mode)
        changed = mode is not self.interaction_mode
        self.interaction_mode = mode
        return InteractionModeSelectionResult(
            requested=mode,
            changed=changed,
            auto_unrestricted=self.auto_mode_unrestricted,
        )


class StubReasoningController:
    """ReasoningController protocol stub driven entirely in-memory."""

    def __init__(self, effort: ReasoningEffort = ReasoningEffort.HIGH) -> None:
        self.reasoning_effort = effort
        self.effective_reasoning_effort = effort.effective
        self.calls: list[ReasoningEffort] = []

    async def set_reasoning_effort(
        self, effort: ReasoningEffort
    ) -> ReasoningEffortSelectionResult:
        self.calls.append(effort)
        changed = effort is not self.reasoning_effort
        self.reasoning_effort = effort
        self.effective_reasoning_effort = effort.effective
        return ReasoningEffortSelectionResult(
            requested=effort,
            effective=effort.effective,
            changed=changed,
        )


class StubProviderController:
    """ProviderController protocol stub with a result lookup table."""

    def __init__(
        self,
        profiles: tuple[ProviderOption, ...] = (),
        selected_profile: str = "",
    ) -> None:
        self.profiles = profiles
        self.selected_profile = selected_profile
        self.results: dict[str, ProviderSelectionResult] = {}
        self.calls: list[str] = []

    async def select_profile(self, name: str) -> ProviderSelectionResult:
        self.calls.append(name)
        return self.results[name]


class StubUiPreferences:
    """UiPreferencesStore stub that records every save."""

    def __init__(self) -> None:
        self.saved_language: UiLanguage | None = None
        self.saved_effort: ReasoningEffort | None = None
        self.saved_mode: InteractionMode | None = None

    async def load_language(self) -> UiLanguage:
        return UiLanguage.SIMPLIFIED_CHINESE

    async def load_reasoning_effort(self) -> ReasoningEffort:
        return ReasoningEffort.HIGH

    async def load_interaction_mode(self) -> InteractionMode:
        return InteractionMode.NORMAL

    async def save_language(self, language: UiLanguage) -> None:
        self.saved_language = language

    async def save_reasoning_effort(self, effort: ReasoningEffort) -> None:
        self.saved_effort = effort

    async def save_interaction_mode(self, mode: InteractionMode) -> None:
        self.saved_mode = mode


class StubProviderSettingsStore:
    """In-memory ProviderSettingsStore that records writes."""

    def __init__(self, settings: ManagedProviderSettings | None = None) -> None:
        self._settings = (
            settings if settings is not None else ManagedProviderSettings()
        )
        self.saved: list[ManagedProviderProfile] = []
        self.deleted: list[str] = []

    async def load(self) -> ManagedProviderSettings:
        return self._settings

    async def save_profile(
        self,
        profile: ManagedProviderProfile,
        *,
        make_default: bool = True,
    ) -> ManagedProviderSettings:
        self.saved.append(profile)
        profiles = tuple(
            entry for entry in self._settings.profiles if entry.name != profile.name
        ) + (profile,)
        default = profile.name if make_default else self._settings.default_provider
        self._settings = ManagedProviderSettings(profiles, default)
        return self._settings

    async def set_default(self, name: str) -> ManagedProviderSettings:
        if self._settings.profile(name) is None:
            return self._settings
        self._settings = ManagedProviderSettings(self._settings.profiles, name)
        return self._settings

    async def delete_profile(self, name: str) -> ManagedProviderSettings:
        self.deleted.append(name)
        self._settings = ManagedProviderSettings(
            tuple(entry for entry in self._settings.profiles if entry.name != name),
            (
                None
                if self._settings.default_provider == name
                else self._settings.default_provider
            ),
        )
        return self._settings


class _SettingsHost(App[None]):
    """Minimal host that pushes a ModalScreen so it can be driven headlessly.

    Textual delivers a dismissed screen's result to the ``push_screen`` callback
    rather than storing it on the screen, so the host records it here.
    """

    def __init__(self, screen: ProviderSettingsScreen) -> None:
        super().__init__()
        self._test_screen = screen
        self.dismissed = False
        self.dismissed_result: object | None = None

    def _record_dismiss(self, result: object | None) -> None:
        self.dismissed = True
        self.dismissed_result = result

    def on_mount(self) -> None:
        self.push_screen(self._test_screen, self._record_dismiss)


def _make_app(**kwargs: object) -> EvilCodeApp:
    defaults: dict[str, object] = {
        "provider_name": "test",
        "model_name": "test-model",
        "cwd": Path.cwd(),
    }
    defaults.update(kwargs)
    return EvilCodeApp(FakeRunner(), **defaults)  # type: ignore[arg-type]


def _settings_screen(
    store: StubProviderSettingsStore,
    settings: ManagedProviderSettings | None = None,
    **kwargs: object,
) -> ProviderSettingsScreen:
    resolved = settings if settings is not None else ManagedProviderSettings()
    return ProviderSettingsScreen(
        language=UiLanguage.SIMPLIFIED_CHINESE,
        provider_settings=resolved,
        provider_settings_store=store,
        provider_catalog=None,
        **kwargs,
    )


def _deepseek_profile() -> ManagedProviderProfile:
    return ManagedProviderProfile(
        name="deepseek",
        protocol="openai-chat",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        api_key="sk-test",
    )


# ─── Interaction-mode cycling + apply ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_action_cycle_interaction_mode_cycles_all_modes():
    """action_cycle_interaction_mode walks NORMAL->ACCEPT_EDITS->PLAN->AUTO->NORMAL."""
    controller = StubInteractionModeController(InteractionMode.NORMAL)
    prefs = StubUiPreferences()
    app = _make_app(interaction_mode_controller=controller, ui_preferences=prefs)
    async with app.run_test(size=(100, 30)) as pilot:
        expected = (
            InteractionMode.ACCEPT_EDITS,
            InteractionMode.PLAN,
            InteractionMode.AUTO,
            InteractionMode.NORMAL,
        )
        for mode in expected:
            await app.action_cycle_interaction_mode()
            await pilot.pause()
            assert app._interaction_mode is mode
            assert controller.calls[-1] is mode

        # Each change posts a status entry; AUTO is reported with the
        # limited-auto key because the stub is not auto-unrestricted.
        changed = [
            entry
            for entry in app._entries
            if entry.ui_key in {"mode.changed", "mode.changed_auto_limited"}
        ]
        assert len(changed) == len(expected)
        assert any(
            entry.ui_key == "mode.changed_auto_limited" for entry in app._entries
        )
        assert prefs.saved_mode is InteractionMode.NORMAL

        # The runtime-bar mode widget reflects the final mode.
        mode_widget = app.query_one("#runtime-mode", Static)
        rendered = str(mode_widget.render())
        assert "◇" in rendered
        assert "normal" in rendered


@pytest.mark.asyncio
async def test_shift_tab_binding_cycles_interaction_mode():
    """The shift+tab key binding drives action_cycle_interaction_mode."""
    controller = StubInteractionModeController(InteractionMode.NORMAL)
    app = _make_app(interaction_mode_controller=controller)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("shift+tab")
        await pilot.pause()
        assert app._interaction_mode is InteractionMode.ACCEPT_EDITS


@pytest.mark.asyncio
async def test_escape_binding_cancels_running_turn():
    """The escape key binding cancels an in-flight turn worker."""
    assert any(
        binding.key == "escape" and binding.action == "cancel_turn"
        for binding in EvilCodeApp.BINDINGS
    )

    class FakeWorker:
        is_running = True
        cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    app = _make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        app._turn_worker = FakeWorker()  # type: ignore[assignment]
        await pilot.press("escape")
        await pilot.pause()

        assert app._turn_worker.cancelled
        assert any(
            entry.ui_key == "turn.cancel_requested" for entry in app._entries
        )


class StubSandboxManager:
    def status(self) -> dict:
        return {"mode": "auto", "egress": "allowlist", "projects": 1, "max_projects": 10}

    def list_projects(self) -> list[dict]:
        return [{
            "project_id": "p1",
            "lifecycle": "persistent",
            "importance": 3.0,
            "protected": False,
            "volume": "code-agent-p1-work",
        }]

    async def gc(self) -> dict:
        return {"projects_evicted": 1}

    async def clean_cache(self) -> dict:
        return {}

    def protect_project(self, project_id: str) -> bool:
        return True

    def unprotect_project(self, project_id: str) -> bool:
        return True

    def remove_project(self, project_id: str) -> bool:
        return True


@pytest.mark.asyncio
async def test_sandbox_slash_command_status_and_list():
    """/sandbox status 与 /sandbox list 在 TUI 中输出可读结果。"""
    app = _make_app(sandbox_manager=StubSandboxManager())
    async with app.run_test(size=(100, 30)) as pilot:
        await app._run_sandbox_command("status")
        await pilot.pause()
        assert any("模式=auto" in e.text for e in app._entries)

        await app._run_sandbox_command("list")
        await pilot.pause()
        assert any("p1" in e.text for e in app._entries)
        assert any("persistent" in e.text for e in app._entries)


@pytest.mark.asyncio
async def test_sandbox_slash_command_unavailable():
    app = _make_app(sandbox_manager=None)
    async with app.run_test(size=(100, 30)) as pilot:
        await app._run_sandbox_command("status")
        await pilot.pause()
        assert any("沙箱管理器" in e.text for e in app._entries)


class StubDraftManager:
    def list_drafts(self) -> list[dict]:
        return [{
            "draft_id": "d1",
            "merged": False,
            "branch": "code-agent-draft-d1",
            "root": "C:/drafts/d1",
            "created_at": 0.0,
        }]

    async def create(self, root: str):
        from code_agent.sandbox.draft import DraftRecord

        return DraftRecord(
            draft_id="d1",
            project_id="p1",
            project_root=root,
            root="C:/drafts/d1",
            branch="code-agent-draft-d1",
            base_commit="abc",
            created_at=0.0,
            volume="code-agent-d1-work",
        )

    async def status(self, draft_id: str) -> dict:
        return {
            "draft_id": draft_id,
            "root": "C:/drafts/d1",
            "branch": "code-agent-draft-d1",
            "at_base": True,
            "changed": "(clean)",
        }

    async def merge(self, draft_id: str) -> str:
        return f"merged {draft_id}"

    async def discard(self, draft_id: str) -> str:
        return f"discarded {draft_id}"


@pytest.mark.asyncio
async def test_draft_slash_command_list():
    """/draft list 在 TUI 中输出副本列表。"""
    app = _make_app(draft_manager=StubDraftManager())
    async with app.run_test(size=(100, 30)) as pilot:
        await app._run_draft_command("list")
        await pilot.pause()
        assert any("d1" in e.text for e in app._entries)
        assert any("code-agent-draft-d1" in e.text for e in app._entries)


@pytest.mark.asyncio
async def test_apply_interaction_mode_updates_runtime_bar():
    """_apply_interaction_mode sets state, refreshes the mode widget and posts a status."""
    controller = StubInteractionModeController(InteractionMode.NORMAL)
    app = _make_app(interaction_mode_controller=controller)
    async with app.run_test(size=(100, 30)) as pilot:
        await app._apply_interaction_mode(InteractionMode.PLAN)
        await pilot.pause()

        assert app._interaction_mode is InteractionMode.PLAN
        rendered = str(app.query_one("#runtime-mode", Static).render())
        assert "▣" in rendered
        assert "plan" in rendered
        assert any(entry.ui_key == "mode.changed" for entry in app._entries)


@pytest.mark.asyncio
async def test_apply_interaction_mode_same_mode_posts_already_selected():
    """Re-applying the current mode posts mode.already_selected without a status change."""
    controller = StubInteractionModeController(InteractionMode.PLAN)
    app = _make_app(interaction_mode_controller=controller)
    async with app.run_test(size=(100, 30)) as pilot:
        await app._apply_interaction_mode(InteractionMode.PLAN)
        await pilot.pause()
        assert any(entry.ui_key == "mode.already_selected" for entry in app._entries)
        assert not any(entry.ui_key == "mode.changed" for entry in app._entries)


@pytest.mark.asyncio
async def test_apply_interaction_mode_without_controller_is_graceful():
    """Without a controller the app reports mode.unavailable instead of crashing."""
    app = _make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        await app._apply_interaction_mode(InteractionMode.AUTO)
        await pilot.pause()
        assert any(entry.ui_key == "mode.unavailable" for entry in app._entries)
        assert app._interaction_mode is InteractionMode.NORMAL


# ─── Language switching ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_language_switch_relocalizes_chrome_and_saves():
    """Switching language flips app._language, re-localizes chrome and saves the choice."""
    prefs = StubUiPreferences()
    app = _make_app(ui_preferences=prefs)
    async with app.run_test(size=(100, 30)) as pilot:
        assert app._language is UiLanguage.SIMPLIFIED_CHINESE
        prompt = app.query_one("#prompt", Input)
        shortcut_bar = app.query_one("#shortcut-bar", Static)
        before_placeholder = prompt.placeholder
        before_shortcuts = str(shortcut_bar.render())

        await app._language_settings_selected(UiLanguage.ENGLISH)
        await pilot.pause()

        assert app._language is UiLanguage.ENGLISH
        assert prompt.placeholder != before_placeholder
        assert prompt.placeholder == ui_text(UiLanguage.ENGLISH, "prompt.placeholder")
        assert str(shortcut_bar.render()) != before_shortcuts
        assert prefs.saved_language is UiLanguage.ENGLISH
        assert any(entry.ui_key == "settings.changed" for entry in app._entries)


@pytest.mark.asyncio
async def test_language_switch_same_language_is_noop():
    """Selecting the already-active language does not re-save the preference."""
    prefs = StubUiPreferences()
    app = _make_app(ui_preferences=prefs)
    async with app.run_test(size=(100, 30)) as pilot:
        await app._language_settings_selected(UiLanguage.SIMPLIFIED_CHINESE)
        await pilot.pause()
        assert prefs.saved_language is None
        assert app._language is UiLanguage.SIMPLIFIED_CHINESE


@pytest.mark.asyncio
async def test_language_switch_persists_with_real_store(tmp_path):
    """A real JsonUiPreferencesStore persists the selected language."""
    store = JsonUiPreferencesStore(tmp_path / "ui.json")
    app = _make_app(ui_preferences=store)
    async with app.run_test(size=(100, 30)) as pilot:
        await app._language_settings_selected(UiLanguage.ENGLISH)
        await pilot.pause()
        assert await store.load_language() is UiLanguage.ENGLISH


# ─── Provider-selection apply ──────────────────────────────────────────────────


def _switched_result(
    *,
    changed: bool,
    context_window_tokens: int | None = 128_000,
) -> ProviderSelectionResult:
    return ProviderSelectionResult(
        profile_name="deepseek",
        provider_name="DeepSeek",
        model_name="deepseek-v4-pro",
        previous_session_id=None,
        changed=changed,
        stopped_background_tasks=0,
        context_window_tokens=context_window_tokens,
    )


@pytest.mark.asyncio
async def test_apply_provider_selection_switches_profile():
    """A changed provider updates names, resets context tokens and posts provider.switched."""
    controller = StubProviderController()
    controller.results["deepseek"] = _switched_result(changed=True)
    app = _make_app(provider_controller=controller)
    async with app.run_test(size=(100, 30)) as pilot:
        app._context_used_tokens = 500
        await app._apply_provider_selection("deepseek")
        await pilot.pause()

        assert controller.calls == ["deepseek"]
        assert app._provider_name == "DeepSeek"
        assert app._model_name == "deepseek-v4-pro"
        assert app._context_window_tokens == 128_000
        assert app._context_used_tokens == 0
        assert app._context_usage_estimated is True

        model_widget = app.query_one("#runtime-model", Static)
        rendered = str(model_widget.render())
        assert "DeepSeek" in rendered
        assert "deepseek-v4-pro" in rendered

        switched = [entry for entry in app._entries if entry.ui_key == "provider.switched"]
        assert len(switched) == 1
        assert not any(entry.ui_key == "provider.already_selected" for entry in app._entries)


@pytest.mark.asyncio
async def test_apply_provider_selection_same_profile_keeps_context():
    """Re-selecting the active profile posts provider.already_selected without resetting tokens."""
    controller = StubProviderController()
    controller.results["deepseek"] = _switched_result(changed=False)
    app = _make_app(provider_controller=controller)
    async with app.run_test(size=(100, 30)) as pilot:
        app._context_used_tokens = 321
        await app._apply_provider_selection("deepseek")
        await pilot.pause()

        assert any(entry.ui_key == "provider.already_selected" for entry in app._entries)
        assert not any(entry.ui_key == "provider.switched" for entry in app._entries)
        # Context usage is preserved when the same profile stays selected.
        assert app._context_used_tokens == 321


@pytest.mark.asyncio
async def test_apply_provider_selection_saves_default_profile():
    """Selecting a managed profile records the new default through the store."""
    controller = StubProviderController()
    controller.results["deepseek"] = _switched_result(changed=True)
    managed = ManagedProviderSettings((_deepseek_profile(),), "deepseek")
    store = StubProviderSettingsStore(managed)
    app = _make_app(
        provider_controller=controller,
        provider_settings_store=store,
        managed_provider_settings=managed,
    )
    async with app.run_test(size=(100, 30)) as pilot:
        await app._apply_provider_selection("deepseek")
        await pilot.pause()
        assert app._managed_provider_settings is not None
        assert app._managed_provider_settings.default_provider == "deepseek"


# ─── Slash-command dispatch table ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_slash_mode_without_arguments_reports_current():
    app = _make_app(
        interaction_mode_controller=StubInteractionModeController(InteractionMode.NORMAL)
    )
    async with app.run_test(size=(100, 30)) as pilot:
        await app._dispatch_slash_command("/mode")
        await pilot.pause()
        entry = next(e for e in app._entries if e.ui_key == "mode.current")
        assert entry.category == "system"
        assert "normal" in entry.text
        assert "accept-edits" in entry.text


@pytest.mark.asyncio
async def test_slash_mode_argument_switches_mode():
    app = _make_app(
        interaction_mode_controller=StubInteractionModeController(InteractionMode.NORMAL)
    )
    async with app.run_test(size=(100, 30)) as pilot:
        await app._dispatch_slash_command("/mode plan")
        await pilot.pause()
        assert app._interaction_mode is InteractionMode.PLAN
        assert any(e.ui_key == "mode.changed" for e in app._entries)


@pytest.mark.asyncio
async def test_slash_mode_invalid_argument_reports_error():
    app = _make_app(
        interaction_mode_controller=StubInteractionModeController(InteractionMode.NORMAL)
    )
    async with app.run_test(size=(100, 30)) as pilot:
        await app._dispatch_slash_command("/mode nope")
        await pilot.pause()
        entry = next(e for e in app._entries if e.ui_key == "mode.invalid")
        assert entry.category == "error"
        assert dict(entry.ui_values)["value"] == "nope"


@pytest.mark.asyncio
async def test_slash_effort_valid_and_invalid_arguments():
    app = _make_app(
        reasoning_controller=StubReasoningController(ReasoningEffort.LOW)
    )
    async with app.run_test(size=(100, 30)) as pilot:
        await app._dispatch_slash_command("/effort high")
        await pilot.pause()
        assert app._reasoning_effort is ReasoningEffort.HIGH
        assert any(e.ui_key == "effort.changed" for e in app._entries)

        await app._dispatch_slash_command("/effort bogus")
        await pilot.pause()
        assert any(e.ui_key == "effort.invalid" for e in app._entries)
        assert app._reasoning_effort is ReasoningEffort.HIGH


@pytest.mark.asyncio
async def test_slash_status_reports_provider_model_context():
    app = _make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        await app._dispatch_slash_command("/status")
        await pilot.pause()
        entry = next(e for e in app._entries if e.ui_key == "command.status")
        assert entry.category == "system"
        assert "test" in entry.text
        assert "test-model" in entry.text
        # FakeRunner has no session, so the fallback is localized "not created".
        assert dict(entry.ui_values)["session"] == ui_text(
            app._language, "command.not_created"
        )


@pytest.mark.asyncio
async def test_slash_help_posts_command_catalog():
    app = _make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        await app._dispatch_slash_command("/help")
        await pilot.pause()
        assert any(e.ui_key == "command.help" for e in app._entries)


@pytest.mark.asyncio
async def test_slash_clear_empties_transcript():
    app = _make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        app._write_entry("user", "keep me?")
        await pilot.pause()
        # Mount also posts the startup.ready banner entry.
        assert any(entry.category == "user" for entry in app._entries)

        await app._dispatch_slash_command("/clear")
        await pilot.pause()
        assert len(app._entries) == 1
        assert app._entries[0].ui_key == "transcript.cleared"
        # The cleared-banner entry itself is the only remaining widget.
        assert len(app._entry_widgets) == 1


@pytest.mark.asyncio
async def test_slash_cancel_with_nothing_running_posts_status():
    app = _make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        await app._dispatch_slash_command("/cancel")
        await pilot.pause()
        assert any(e.ui_key == "turn.none_running" for e in app._entries)


@pytest.mark.asyncio
async def test_slash_provider_switches_profile():
    controller = StubProviderController()
    controller.results["deepseek"] = _switched_result(changed=True)
    app = _make_app(provider_controller=controller)
    async with app.run_test(size=(100, 30)) as pilot:
        await app._dispatch_slash_command("/provider deepseek")
        await pilot.pause()
        assert controller.calls == ["deepseek"]
        assert app._provider_name == "DeepSeek"
        assert any(e.ui_key == "provider.switched" for e in app._entries)


@pytest.mark.asyncio
async def test_slash_plan_switches_to_plan_mode():
    app = _make_app(
        interaction_mode_controller=StubInteractionModeController(InteractionMode.NORMAL)
    )
    async with app.run_test(size=(100, 30)) as pilot:
        await app._dispatch_slash_command("/plan")
        await pilot.pause()
        assert app._interaction_mode is InteractionMode.PLAN
        assert any(e.ui_key == "mode.changed" for e in app._entries)


@pytest.mark.asyncio
async def test_slash_unknown_command_is_handled_gracefully():
    app = _make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        await app._dispatch_slash_command("/bogus")
        await pilot.pause()
        entry = next(e for e in app._entries if e.ui_key == "command.unknown")
        assert entry.category == "error"
        assert dict(entry.ui_values)["command"] == "bogus"


# ─── Save-and-reload contract ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_provider_settings_submission_sets_reload_return_code():
    """A provider-settings submission exits the app with TUI_RELOAD_PROVIDER_SETTINGS."""
    app = _make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        await app._provider_settings_selected(ProviderSettingsSubmission("deepseek"))
        await pilot.pause()
        assert app.return_code == TUI_RELOAD_PROVIDER_SETTINGS


@pytest.mark.asyncio
async def test_provider_settings_cancel_returns_to_settings():
    """Dismissing provider settings without a result re-opens the settings screen."""
    app = _make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        await app._provider_settings_selected(None)
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)


# ─── Pure helper unit tests ────────────────────────────────────────────────────


def test_bounded_tool_preview_short_content():
    rendered, total, omitted, truncated = ToolFeedbackRenderer.bounded_tool_preview(
        "a\nb\nc\n"
    )
    assert rendered == ("a", "b", "c")
    assert total == 3
    assert omitted == 0
    assert truncated is False


def test_bounded_tool_preview_truncates_long_single_line():
    rendered, total, omitted, truncated = ToolFeedbackRenderer.bounded_tool_preview("x" * 10_000)
    assert total == 1
    assert omitted == 0
    assert truncated is True
    assert len(rendered) == 1
    assert rendered[0].endswith("…")
    assert len(rendered[0]) == 6_000  # _TOOL_OUTPUT_MAX_CHARACTERS


def test_bounded_tool_preview_drops_middle_lines():
    content = "\n".join(f"line {index}" for index in range(100))
    rendered, total, omitted, truncated = ToolFeedbackRenderer.bounded_tool_preview(content)
    assert total == 100
    assert len(rendered) == 40
    assert omitted == 60
    assert truncated is False
    assert rendered[0] == "line 0"
    assert rendered[-1] == "line 99"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({}, "—"),
        ({"duration_seconds": None}, "—"),
        ({"duration_seconds": -1}, "—"),
        ({"duration_seconds": True}, "—"),
        ({"duration_seconds": "0.5"}, "—"),
        ({"duration_seconds": 0.0005}, "<1ms"),
        ({"duration_seconds": 0.5}, "500ms"),
        ({"duration_seconds": 1}, "1.0s"),
        ({"duration_seconds": 3.25}, "3.2s"),
        ({"duration_seconds": 125}, "2m 05s"),
    ],
)
def test_event_duration(data, expected):
    assert EvilCodeApp._event_duration(data) == expected


def test_tool_change_report_search_replace_counts():
    app = _make_app()
    state = ToolFeedbackState(
        call_id="c1",
        name="search_replace",
        arguments={
            "path": "src/a.py",
            "old": "def old():\n    pass",
            "new": "def old():\n    return 1\n    pass",
        },
        entry_index=0,
        phase="completed",
    )
    report = app._tool_feedback_renderer.tool_change_report(state)
    assert report is not None
    assert report["omitted_files"] == 0
    files = report["files"]
    assert files[0]["path"] == "src/a.py"
    assert files[0]["status"] == "modified"
    assert files[0]["additions"] == 1
    assert files[0]["deletions"] == 0


def test_tool_change_report_apply_patch_counts():
    app = _make_app()
    patch = "--- a/f.py\n+++ b/f.py\n@@ -1,2 +1,2 @@\n-a\n+b\n"
    state = ToolFeedbackState(
        call_id="c2",
        name="apply_patch",
        arguments={"path": "f.py", "patch": patch},
        entry_index=0,
        phase="completed",
    )
    report = app._tool_feedback_renderer.tool_change_report(state)
    assert report is not None
    files = report["files"]
    assert files[0]["path"] == "f.py"
    assert files[0]["additions"] == 1
    assert files[0]["deletions"] == 1


def test_tool_change_report_none_for_incomplete_state():
    app = _make_app()
    state = ToolFeedbackState(
        call_id="c3",
        name="search_replace",
        arguments={},
        entry_index=0,
        phase="requested",
    )
    assert app._tool_feedback_renderer.tool_change_report(state) is None


def test_tool_change_report_passthrough_workspace_changes():
    app = _make_app()
    changes = {
        "files": [{"path": "a.txt", "status": "created", "additions": 3, "deletions": 0}],
        "omitted_files": 0,
        "scan_limited": False,
    }
    state = ToolFeedbackState(
        call_id="c4",
        name="bash",
        arguments={},
        entry_index=0,
        phase="completed",
        workspace_changes=changes,
    )
    assert app._tool_feedback_renderer.tool_change_report(state) is changes


def test_context_metrics_thresholds():
    app = _make_app()
    app._context_window_tokens = 1000
    app._context_used_tokens = 0
    app._context_usage_estimated = False

    app._context_used_tokens = 200
    assert app._context_percentage() == "20.0%"
    assert app._context_color() == "#78c2a4"

    app._context_used_tokens = 600
    assert app._context_percentage() == "60.0%"
    assert app._context_color() == "#e59c74"

    app._context_used_tokens = 900
    assert app._context_percentage() == "90.0%"
    assert app._context_color() == "#c76d6d"


def test_context_percentage_tiny_and_unknown():
    app = _make_app()
    app._context_window_tokens = 2000
    app._context_used_tokens = 1
    app._context_usage_estimated = True
    assert app._context_percentage() == "~<0.1%"

    app._context_window_tokens = None
    assert app._context_percentage() == ui_text(
        app._language, "runtime.context_unknown"
    )
    assert app._context_color() == "#8e939a"


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("provider", "x", "bold #7da7d9"),
        ("model", "x", "bold #7da7d9"),
        ("profile", "x", "bold #7da7d9"),
        ("source", "x", "bold #7da7d9"),
        ("name", "x", "bold #8ed1e6"),
        ("task_id", "x", "bold #8ed1e6"),
        ("session_id", "x", "bold #8ed1e6"),
        ("title", "x", "bold #8ed1e6"),
        ("cwd", "/x", "#8ab3ad"),
        ("path", "/x", "#8ab3ad"),
        ("effect", "allow", "bold #78c2a4"),
        ("outcome", "x", "bold #78c2a4"),
        ("status", "x", "bold #78c2a4"),
        ("duration", "5", "bold #78c2a4"),
        ("steps", "2", "bold #78c2a4"),
        ("step", "2", "bold #78c2a4"),
        ("context", "50.0%", "bold #8ab3ad"),
        ("effort", "high", "bold #78c2a4"),
        ("effort", "bogus", "bold #a9a1e8"),
        ("mode", "auto", "bold #d58cc8"),
        ("mode", "bogus", "bold #8b9cff"),
        ("policy", "x", "#9aa3b2"),
        ("message", "x", "#d88a8a"),
        ("reason", "x", "#d88a8a"),
        ("error", "x", "#d88a8a"),
        ("unknown_name", "x", None),
    ],
)
def test_semantic_value_style(name, value, expected):
    assert EvilCodeApp._semantic_value_style(name, value) == expected


def test_display_cwd_collapses_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    app = _make_app(cwd=home / "projects" / "evil")
    assert app._display_cwd() == "~/" + str(Path("projects") / "evil")


def test_display_cwd_home_root_is_tilde(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    app = _make_app(cwd=home)
    assert app._display_cwd() == "~"


def test_display_cwd_outside_home_not_collapsed(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    app = _make_app(cwd=outside)
    assert app._display_cwd() == str(outside.resolve())


# ─── CollapsingPulseAnimation ──────────────────────────────────────────────────


def test_pulse_animation_initial_levels():
    animation = CollapsingPulseAnimation()
    assert animation.phase == "moving"
    assert animation.peak_position == 0
    assert animation.direction == 1
    assert animation.levels() == (7, 0, 0, 0, 0, 0, 0)


def test_pulse_animation_advance_moves_peak():
    animation = CollapsingPulseAnimation()
    animation.advance()
    assert animation.peak_position == 1
    assert animation.levels() == (5, 7, 0, 0, 0, 0, 0)


def test_pulse_animation_edge_then_collapse_then_reverse():
    animation = CollapsingPulseAnimation()
    for _ in range(6):
        animation.advance()
    assert animation.peak_position == 6
    assert animation.phase == "edge-hold"

    animation.advance()
    assert animation.phase == "collapsing"
    assert animation.merged_trail_count == 1

    animation.advance()
    assert animation.merged_trail_count == 2

    animation.advance()
    assert animation.phase == "merged-hold"

    animation.advance()
    assert animation.direction == -1
    assert animation.peak_position == 5
    assert animation.phase == "moving"


def test_pulse_animation_reset_restores_initial_state():
    animation = CollapsingPulseAnimation()
    for _ in range(12):
        animation.advance()
    animation.reset()
    assert animation.peak_position == 0
    assert animation.direction == 1
    assert animation.merged_trail_count == 0
    assert animation.phase == "moving"
    assert animation.levels() == (7, 0, 0, 0, 0, 0, 0)


def test_pulse_animation_delay_seconds_by_phase():
    animation = CollapsingPulseAnimation()
    assert animation.delay_seconds == 0.09
    animation.phase = "edge-hold"
    assert animation.delay_seconds == 0.14
    animation.phase = "collapsing"
    assert animation.delay_seconds == 0.10
    animation.phase = "merged-hold"
    assert animation.delay_seconds == 0.22


# ─── ProviderSettingsScreen flows ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_provider_settings_new_profile_saves_and_dismisses():
    stub = StubProviderSettingsStore()
    screen = _settings_screen(stub)
    host = _SettingsHost(screen)
    async with host.run_test(size=(100, 40)) as pilot:
        screen.query_one("#provider-settings-name", Input).value = "myprovider"
        screen.query_one("#provider-settings-model", Input).value = "my-model-1"
        screen.query_one("#provider-settings-api-key", Input).value = "sk-myprovider"

        await screen._save_provider()
        await pilot.pause()

        assert len(stub.saved) == 1
        profile = stub.saved[0]
        assert profile.name == "myprovider"
        assert profile.model == "my-model-1"
        assert profile.protocol == "openai-responses"  # default openai preset
        assert profile.base_url == "https://api.openai.com/v1"
        assert profile.api_key == "sk-myprovider"
        assert host.dismissed
        assert host.dismissed_result == ProviderSettingsSubmission("myprovider")


@pytest.mark.asyncio
async def test_provider_settings_new_profile_requires_api_key():
    stub = StubProviderSettingsStore()
    screen = _settings_screen(stub)
    host = _SettingsHost(screen)
    async with host.run_test(size=(100, 40)) as pilot:
        screen.query_one("#provider-settings-name", Input).value = "myprovider"
        screen.query_one("#provider-settings-model", Input).value = "my-model-1"

        await screen._save_provider()
        await pilot.pause()

        assert stub.saved == []
        assert host.dismissed is False
        error_widget = screen.query_one("#provider-settings-error", Static)
        assert str(error_widget.render())  # non-empty error message


@pytest.mark.asyncio
async def test_provider_settings_edit_profile_populates_form():
    profile = _deepseek_profile()
    settings = ManagedProviderSettings((profile,), "deepseek")
    stub = StubProviderSettingsStore(settings)
    screen = _settings_screen(stub, settings)
    host = _SettingsHost(screen)
    async with host.run_test(size=(100, 40)) as pilot:
        screen._edit_profile("deepseek")
        await pilot.pause()

        assert screen._editing_profile == "deepseek"
        name_input = screen.query_one("#provider-settings-name", Input)
        assert name_input.value == "deepseek"
        assert name_input.disabled is True
        assert screen.query_one("#provider-settings-model", Input).value == "deepseek-v4-pro"
        assert screen.query_one("#provider-settings-base-url", Input).value == (
            "https://api.deepseek.com"
        )
        assert screen.query_one("#provider-settings-api-key", Input).value == ""
        assert screen._active_preset == "deepseek"
        assert screen.query_one("#provider-settings-delete", Button).disabled is False


@pytest.mark.asyncio
async def test_provider_settings_new_profile_resets_form():
    profile = _deepseek_profile()
    settings = ManagedProviderSettings((profile,), "deepseek")
    stub = StubProviderSettingsStore(settings)
    screen = _settings_screen(stub, settings)
    host = _SettingsHost(screen)
    async with host.run_test(size=(100, 40)) as pilot:
        screen._new_profile()
        await pilot.pause()

        assert screen._editing_profile is None
        name_input = screen.query_one("#provider-settings-name", Input)
        assert name_input.disabled is False
        assert name_input.value == ""
        assert screen.query_one("#provider-settings-model", Input).value == ""
        assert screen.query_one("#provider-settings-api-key", Input).value == ""
        assert screen._active_preset == "openai"
        assert screen.query_one("#provider-settings-delete", Button).disabled is True


@pytest.mark.asyncio
async def test_provider_settings_delete_requires_confirmation_then_dismisses():
    profile = _deepseek_profile()
    settings = ManagedProviderSettings((profile,), "deepseek")
    stub = StubProviderSettingsStore(settings)
    screen = _settings_screen(stub, settings)
    host = _SettingsHost(screen)
    async with host.run_test(size=(100, 40)) as pilot:
        screen._edit_profile("deepseek")
        await pilot.pause()

        # First delete press only asks for confirmation.
        await screen._delete_provider()
        await pilot.pause()
        assert screen._delete_confirmation_for == "deepseek"
        assert stub.deleted == []
        assert host.dismissed is False
        delete_button = screen.query_one("#provider-settings-delete", Button)
        assert delete_button.variant == "error"

        # Confirmed delete removes the profile and dismisses with operation="deleted".
        await screen._delete_provider()
        await pilot.pause()
        assert stub.deleted == ["deepseek"]
        assert host.dismissed
        assert host.dismissed_result == ProviderSettingsSubmission(
            "deepseek", operation="deleted"
        )
