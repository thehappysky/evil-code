# [Phase 7] 2026-08-05: PermissionManager × sandbox 联动。
"""Tests for sandbox auto-allow in PermissionManager."""

from __future__ import annotations

import pytest

from code_agent.permission.manager import Decision, PermissionManager


@pytest.mark.asyncio
async def test_risky_command_auto_allowed_when_sandbox_available():
    async def available():
        return True

    pm = PermissionManager(
        prompt_policy="deny",
        sandbox_mode="auto",
        sandbox_available=available,
    )
    decision = await pm.decide("shell_exec", {"command": "rm -rf /tmp/x"})
    assert decision is Decision.ALLOW
    assert pm.last_reason.startswith("sandbox_auto:clean")


@pytest.mark.asyncio
async def test_risky_command_denied_when_sandbox_unavailable_headless():
    async def available():
        return False

    pm = PermissionManager(
        prompt_policy="deny",
        sandbox_mode="auto",
        sandbox_available=available,
    )
    decision = await pm.decide("shell_exec", {"command": "rm -rf /tmp/x"})
    assert decision is Decision.DENY
    assert pm.last_reason.startswith("sandbox_unavailable:clean")


@pytest.mark.asyncio
async def test_risky_command_asks_when_sandbox_unavailable_interactive():
    async def available():
        return False

    pm = PermissionManager(
        prompt_policy="ask",
        sandbox_mode="auto",
        sandbox_available=available,
    )
    decision = await pm.decide("shell_exec", {"command": "rm -rf /tmp/x"})
    assert decision is Decision.ASK
    assert pm.last_reason.startswith("sandbox_unavailable:clean")


@pytest.mark.asyncio
async def test_sandbox_off_keeps_legacy_behavior():
    pm = PermissionManager(prompt_policy="deny", sandbox_mode="off")
    decision = await pm.decide("shell_exec", {"command": "rm -rf /tmp/x"})
    assert decision is Decision.DENY


@pytest.mark.asyncio
async def test_safe_command_still_local_even_with_sandbox():
    async def available():
        return True

    pm = PermissionManager(
        prompt_policy="deny",
        sandbox_mode="auto",
        sandbox_available=available,
    )
    decision = await pm.decide("shell_exec", {"command": "ls -la"})
    assert decision is Decision.ALLOW
    assert pm.last_reason == ""
