"""Tests for hooks/phase4_hooks.py: PermissionHook + LifecycleHook.

PermissionHook maps PermissionManager.Decision -> HookDecision (the security
boundary between the permission pipeline and the Agent loop). LifecycleHook
adapts the HookDispatcher event bus to the AgentHook interface.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from code_agent.core.hooks import HookDecision
from code_agent.core.session import Session
from code_agent.hooks.dispatcher import HookDispatcher, HookEvent, HookResult
from code_agent.hooks.phase4_hooks import LifecycleHook, PermissionHook
from code_agent.permission.manager import Decision, PermissionManager
from code_agent.tools.result import ToolResult


class TestPermissionHook:
    async def _hook(self, decision: Decision) -> tuple[PermissionHook, AsyncMock]:
        pm = AsyncMock(spec=PermissionManager)
        pm.decide.return_value = decision
        return PermissionHook(pm), pm

    async def test_allow_maps_to_allow(self):
        hook, pm = await self._hook(Decision.ALLOW)
        assert await hook.on_tool_call("bash", {"command": "ls"}) is HookDecision.ALLOW
        pm.decide.assert_awaited_once_with("bash", {"command": "ls"})

    async def test_deny_maps_to_deny(self):
        hook, _ = await self._hook(Decision.DENY)
        assert await hook.on_tool_call("bash", {"command": "rm -rf /"}) is HookDecision.DENY

    async def test_ask_denies_by_default_fail_closed(self):
        # [2026-08-04] Fail-closed: ASK -> DENY by default (Rust PromptPolicy::Deny).
        # Non-pre-approved tools are denied in headless/non-interactive mode.
        hook, _ = await self._hook(Decision.ASK)
        assert await hook.on_tool_call("bash", {"command": "odd cmd"}) is HookDecision.DENY

    async def test_ask_allow_is_explicit_opt_in(self):
        # Legacy allow-by-default contract is preserved behind ask_action="allow".
        hook, _ = await self._hook(Decision.ASK)
        hook._ask_action = "allow"
        assert await hook.on_tool_call("bash", {"command": "odd cmd"}) is HookDecision.ALLOW

    async def test_ask_prompt_delegates_to_console(self, monkeypatch):
        # Interactive REPL: ask_action="prompt" → console confirmation.
        hook, _ = await self._hook(Decision.ASK)
        hook._ask_action = "prompt"
        monkeypatch.setattr("builtins.input", lambda _prompt: "y")
        assert await hook.on_tool_call("bash", {"command": "odd cmd"}) is HookDecision.ALLOW
        monkeypatch.setattr("builtins.input", lambda _prompt: "n")
        assert await hook.on_tool_call("bash", {"command": "odd cmd"}) is HookDecision.DENY

    async def test_exception_treated_as_deny(self):
        pm = AsyncMock(spec=PermissionManager)
        pm.decide.side_effect = RuntimeError("boom")
        hook = PermissionHook(pm)
        assert await hook.on_tool_call("bash", {"command": "ls"}) is HookDecision.DENY


class TestLifecycleHook:
    def _dispatcher_with_handler(self, event: HookEvent) -> tuple[HookDispatcher, AsyncMock]:
        dispatcher = HookDispatcher()
        handler = AsyncMock(return_value=HookResult())
        dispatcher.register(event, handler)
        return dispatcher, handler

    async def test_session_start_dispatches(self):
        dispatcher, handler = self._dispatcher_with_handler(HookEvent.SESSION_START)
        hook = LifecycleHook(dispatcher)
        session = Session(system_prompt="T")
        await hook.on_session_start(session)
        handler.assert_awaited_once()
        assert handler.await_args.args[0] is HookEvent.SESSION_START

    async def test_turn_start_dispatches(self):
        dispatcher, handler = self._dispatcher_with_handler(HookEvent.TURN_START)
        hook = LifecycleHook(dispatcher)
        session = Session(system_prompt="T")
        await hook.on_turn_start(session, 3, [])
        handler.assert_awaited_once()
        ctx = handler.await_args.args[1]
        assert ctx["turn"] == 3

    async def test_pre_tool_deny_stops_chain(self):
        dispatcher, handler = self._dispatcher_with_handler(HookEvent.PRETOOL_USE)
        handler.return_value = HookResult(action="deny", message="no")
        hook = LifecycleHook(dispatcher)
        assert await hook.on_tool_call("bash", {"command": "ls"}) is HookDecision.DENY

    async def test_pre_tool_modify_applies_arguments(self):
        dispatcher, handler = self._dispatcher_with_handler(HookEvent.PRETOOL_USE)
        handler.return_value = HookResult(action="modify", modified_args={"command": "ls -la"})
        hook = LifecycleHook(dispatcher)
        args = {"command": "ls"}
        assert await hook.on_tool_call("bash", args) is HookDecision.MODIFY
        assert args == {"command": "ls -la"}

    async def test_pre_tool_allow(self):
        dispatcher, _ = self._dispatcher_with_handler(HookEvent.PRETOOL_USE)
        hook = LifecycleHook(dispatcher)
        assert await hook.on_tool_call("bash", {"command": "ls"}) is HookDecision.ALLOW

    async def test_tool_result_success_dispatches_post_tool_use(self):
        dispatcher, handler = self._dispatcher_with_handler(HookEvent.POST_TOOL_USE)
        hook = LifecycleHook(dispatcher)
        result = ToolResult.ok("fine")
        await hook.on_tool_result("bash", {"command": "ls"}, result)
        handler.assert_awaited_once()

    async def test_tool_result_failure_dispatches_failure(self):
        dispatcher, handler = self._dispatcher_with_handler(HookEvent.POST_TOOL_USE_FAILURE)
        hook = LifecycleHook(dispatcher)
        result = ToolResult.error("broken")
        await hook.on_tool_result("bash", {"command": "ls"}, result)
        handler.assert_awaited_once()

    async def test_turn_end_dispatches(self):
        dispatcher, handler = self._dispatcher_with_handler(HookEvent.TURN_END)
        hook = LifecycleHook(dispatcher)
        await hook.on_turn_end(Session(system_prompt="T"), 2)
        handler.assert_awaited_once()

    async def test_session_end_dispatches(self):
        dispatcher, handler = self._dispatcher_with_handler(HookEvent.SESSION_END)
        hook = LifecycleHook(dispatcher)
        await hook.on_session_end(Session(system_prompt="T"), reason="completed")
        handler.assert_awaited_once()
        ctx = handler.await_args.args[1]
        assert ctx["reason"] == "completed"

    async def test_handler_exception_degrades_gracefully(self):
        dispatcher = HookDispatcher()
        failing = AsyncMock(side_effect=RuntimeError("boom"))
        dispatcher.register(HookEvent.TURN_END, failing)
        hook = LifecycleHook(dispatcher)
        # Must not raise.
        await hook.on_turn_end(Session(system_prompt="T"), 0)
