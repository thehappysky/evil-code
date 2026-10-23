# [Phase 5] 2026-08-04: Tests for plugin hook registration helpers in cli/main.py
"""Tests for ``_hook_event_name_for`` / ``_register_plugin_hook``.

These helpers wire plugin ``hooks.py`` callables into the ``HookDispatcher``
by a name convention, replacing the previous bare ``pass`` that discarded
every plugin handler.
"""

from __future__ import annotations

import pytest

from code_agent.plugins.registry import hook_event_name_for, register_plugin_hook
from code_agent.hooks.dispatcher import HookDispatcher, HookEvent, HookResult


class TestHookEventNameFor:
    def test_known_event_names(self):
        assert hook_event_name_for("pre_tool_use") == "pre_tool_use"
        assert hook_event_name_for("post_tool_use") == "post_tool_use"
        assert hook_event_name_for("session_start") == "session_start"
        assert hook_event_name_for("turn_end") == "turn_end"

    def test_on_prefix_stripped(self):
        assert hook_event_name_for("on_turn_start") == "turn_start"
        assert hook_event_name_for("on_pre_tool_use") == "pre_tool_use"

    def test_unknown_name_returns_none(self):
        assert hook_event_name_for("on_random_thing") is None
        assert hook_event_name_for("") is None


class TestRegisterPluginHook:
    def _dispatcher(self) -> HookDispatcher:
        return HookDispatcher()

    async def test_registers_handler_under_matching_event(self):
        dispatcher = self._dispatcher()
        calls = []

        async def pre_tool_use(context):
            calls.append(context)
            return HookResult(action="deny", message="blocked")

        assert register_plugin_hook(dispatcher, pre_tool_use) is True
        result = await dispatcher.dispatch(
            HookEvent.PRETOOL_USE, {"tool_name": "bash"},
        )
        assert result.action == "deny"
        assert result.message == "blocked"
        assert calls == [{"tool_name": "bash"}]

    async def test_sync_handler_supported(self):
        dispatcher = self._dispatcher()

        def post_tool_use(context):
            return {"action": "allow"}

        assert register_plugin_hook(dispatcher, post_tool_use) is True
        result = await dispatcher.dispatch(HookEvent.POST_TOOL_USE, {})
        assert result.action == "allow"

    async def test_dict_return_normalized_to_hook_result(self):
        dispatcher = self._dispatcher()

        async def turn_end(context):
            return {"action": "allow", "message": "hi"}

        register_plugin_hook(dispatcher, turn_end)
        result = await dispatcher.dispatch(HookEvent.TURN_END, {})
        assert result.action == "allow"
        assert result.message == "hi"

    async def test_handler_exception_degrades_to_allow(self):
        dispatcher = self._dispatcher()

        async def pre_tool_use(context):
            raise RuntimeError("boom")

        register_plugin_hook(dispatcher, pre_tool_use)
        # Dispatch must not raise; handler failure → allow (best-effort).
        result = await dispatcher.dispatch(HookEvent.PRETOOL_USE, {})
        assert result.action == "allow"

    async def test_unknown_name_not_registered(self):
        dispatcher = self._dispatcher()

        async def on_random_thing(context):
            return HookResult(action="deny")

        assert register_plugin_hook(dispatcher, on_random_thing) is False
        result = await dispatcher.dispatch(HookEvent.PRETOOL_USE, {})
        assert result.action == "allow"  # no handler → default allow
