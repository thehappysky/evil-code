# [TUI] 2026-08-01: Tests for the production Evil Code TUI adaptation.
"""Headless Textual tests for the adapted Evil Code TUI.

The TUI is driven through Textual's ``run_test()`` pilot, so these tests run
without a real terminal. They exercise the full prompt -> agent -> transcript
pipeline, the slash-command completion helper, and the ported domain helpers.
"""

from __future__ import annotations

import asyncio
import inspect
import tempfile
from pathlib import Path

import pytest
from textual.widgets import Input

from code_agent.tui.commands import slash_completions
from code_agent.tui.domain import (
    AgentEvent,
    AgentEventKind,
    AgentRunResult,
    InteractionMode,
    ManagedProviderProfile,
    ReasoningEffort,
    UiLanguage,
    estimate_text_tokens,
    redact_sensitive_text,
)
from code_agent.tui.stores import JsonProviderSettingsStore, JsonUiPreferencesStore
from code_agent.tui.tui import EvilCodeApp


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


def _make_app() -> EvilCodeApp:
    return EvilCodeApp(
        FakeRunner(),
        provider_name="test",
        model_name="test-model",
        cwd=Path.cwd(),
    )


@pytest.mark.asyncio
async def test_app_mounts_and_streams_a_turn():
    """Submitting a prompt renders the user message and the agent response."""
    app = _make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        prompt = app.query_one("#prompt", Input)
        assert prompt.has_focus

        prompt.value = "say hi"
        await pilot.press("enter")
        await pilot.pause(0.3)

        entries = app._entries
        categories = [(entry.category, entry.text) for entry in entries]
        assert any(
            category == "user" and "say hi" in text
            for category, text in categories
        ), categories
        assert any(
            category == "assistant" and "hello from agent" in text
            for category, text in categories
        ), categories


@pytest.mark.asyncio
async def test_app_shows_startup_banner():
    """On mount the app writes a ready banner with provider/model/cwd."""
    app = _make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        entries = app._entries
        assert any(
            entry.category == "system" and entry.ui_key == "startup.ready"
            for entry in entries
        )


@pytest.mark.asyncio
async def test_slash_command_completions():
    """Slash-command completions cover the command catalog."""
    matches = slash_completions("/mo")
    assert any(completion.value == "/mode" for completion in matches)
    effort_matches = slash_completions("/effort ")
    assert any(completion.value == "/effort high" for completion in effort_matches)


def test_estimate_text_tokens():
    assert estimate_text_tokens("") == 0
    assert estimate_text_tokens("hello world") >= 1
    # CJK text is charged more per character than ASCII.
    assert estimate_text_tokens("你好") > estimate_text_tokens("ab")


def test_redact_sensitive_text():
    assert "sk-abcdefgh12345678" not in redact_sensitive_text(
        "token sk-abcdefgh12345678 shown"
    )
    assert redact_sensitive_text(
        "key=super-secret-value", explicit_values=("super-secret-value",)
    ) == "key=[REDACTED]"


@pytest.mark.asyncio
async def test_ui_preferences_round_trip(tmp_path):
    store = JsonUiPreferencesStore(tmp_path / "ui.json")
    await store.save_language(UiLanguage.SIMPLIFIED_CHINESE)
    assert await store.load_language() is UiLanguage.SIMPLIFIED_CHINESE
    await store.save_reasoning_effort(ReasoningEffort.XHIGH)
    assert await store.load_reasoning_effort() is ReasoningEffort.XHIGH
    await store.save_interaction_mode(InteractionMode.PLAN)
    assert await store.load_interaction_mode() is InteractionMode.PLAN


@pytest.mark.asyncio
async def test_provider_settings_store_round_trip(tmp_path):
    store = JsonProviderSettingsStore(tmp_path / "profiles.json")
    profile = ManagedProviderProfile(
        name="deepseek",
        protocol="openai-chat",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        api_key="sk-test",
    )
    settings = await store.save_profile(profile)
    assert settings.default_provider == "deepseek"
    assert settings.profile("deepseek").api_key == "sk-test"

    # A blank API key keeps the previously saved key.
    updated = await store.save_profile(
        ManagedProviderProfile(
            name="deepseek",
            protocol="openai-chat",
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            api_key=None,
        )
    )
    assert updated.profile("deepseek").api_key == "sk-test"

    settings = await store.delete_profile("deepseek")
    assert settings.profile("deepseek") is None
