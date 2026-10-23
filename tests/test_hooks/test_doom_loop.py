# [Phase 5 / Phase 6] 2026-08-04: Tests for DoomLoopHook
"""Tests for code_agent/hooks/doom_loop_hook.py."""

from __future__ import annotations

import pytest

from code_agent.core.hooks import HookDecision
from code_agent.hooks.doom_loop_hook import DoomLoopHook


class TestDoomLoopHook:
    async def test_allows_different_calls(self):
        hook = DoomLoopHook(repeat_limit=3)
        assert await hook.on_tool_call("file_read", {"file_path": "a.py"}) is HookDecision.ALLOW
        assert await hook.on_tool_call("file_read", {"file_path": "b.py"}) is HookDecision.ALLOW
        assert await hook.on_tool_call("file_search", {"pattern": "x"}) is HookDecision.ALLOW

    async def test_allows_repeats_below_limit(self):
        hook = DoomLoopHook(repeat_limit=3)
        for _ in range(3):
            assert await hook.on_tool_call("echo", {"text": "hi"}) is HookDecision.ALLOW

    async def test_denies_repeat_past_limit(self):
        hook = DoomLoopHook(repeat_limit=3)
        for _ in range(3):
            await hook.on_tool_call("echo", {"text": "hi"})
        # 4th identical call → DENY
        assert await hook.on_tool_call("echo", {"text": "hi"}) is HookDecision.DENY

    async def test_recoverable_after_different_call(self):
        hook = DoomLoopHook(repeat_limit=2)
        for _ in range(2):
            await hook.on_tool_call("shell_exec", {"command": "ls"})
        assert await hook.on_tool_call("shell_exec", {"command": "ls"}) is HookDecision.DENY
        # A different call slides the window → the repeat is allowed again.
        await hook.on_tool_call("file_read", {"file_path": "x"})
        assert await hook.on_tool_call("shell_exec", {"command": "ls"}) is HookDecision.ALLOW

    async def test_args_matter(self):
        """Same tool with different arguments is NOT a doom loop."""
        hook = DoomLoopHook(repeat_limit=2)
        await hook.on_tool_call("shell_exec", {"command": "ls"})
        await hook.on_tool_call("shell_exec", {"command": "pwd"})
        assert await hook.on_tool_call("shell_exec", {"command": "ls"}) is HookDecision.ALLOW
