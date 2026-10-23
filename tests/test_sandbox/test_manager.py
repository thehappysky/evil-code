# [Phase 7] 2026-08-05: Tests for SandboxManager routing + state.
"""Tests for code_agent/sandbox/manager.py."""

from __future__ import annotations

import time

import pytest

from code_agent.sandbox.manager import SandboxManager, resolve_sandbox_mode
from code_agent.sandbox.manager import _docker_volume_name, _parse_size
from code_agent.sandbox.state import ProjectRecord
from code_agent.tools.result import ToolResult


def test_resolve_sandbox_mode(monkeypatch):
    monkeypatch.delenv("CODE_AGENT_SANDBOX", raising=False)
    assert resolve_sandbox_mode(False) == "auto"
    assert resolve_sandbox_mode(True) == "strict"
    monkeypatch.setenv("CODE_AGENT_SANDBOX", "off")
    assert resolve_sandbox_mode(True) == "off"
    monkeypatch.setenv("CODE_AGENT_SANDBOX", "strict")
    assert resolve_sandbox_mode(False) == "strict"


@pytest.mark.asyncio
async def test_safe_command_runs_locally(tmp_path):
    mgr = SandboxManager(mode="auto", state_path=tmp_path / "state.json")
    result = await mgr.run("echo sandbox-local-ok", timeout=30)
    assert not result.is_error
    assert result.meta is not None
    assert result.meta["mode"] == "local"
    assert result.meta["sandbox"] is False
    assert "sandbox-local-ok" in result.output


@pytest.mark.asyncio
async def test_risky_command_routes_to_sandbox(monkeypatch, tmp_path):
    mgr = SandboxManager(mode="auto", state_path=tmp_path / "state.json")

    async def fake_available(self):
        return True

    async def fake_run_sandbox(self, command, cwd, timeout, decision):
        return ToolResult(
            output="sandboxed-ok",
            meta={
                "sandbox": True,
                "mode": "sandbox",
                "tier": decision.tier,
                "lifecycle": decision.lifecycle,
                "project_id": "test",
                "reason": decision.reason,
            },
        )

    monkeypatch.setattr(SandboxManager, "is_available", fake_available)
    monkeypatch.setattr(SandboxManager, "_run_sandbox", fake_run_sandbox)

    result = await mgr.run("rm -rf /tmp/probe", timeout=30)
    assert not result.is_error
    assert result.output == "sandboxed-ok"
    assert result.meta is not None
    assert result.meta["mode"] == "sandbox"
    assert result.meta["tier"] == "clean"


@pytest.mark.asyncio
async def test_risky_command_falls_back_local_when_docker_unavailable(
    monkeypatch, tmp_path
):
    mgr = SandboxManager(mode="auto", state_path=tmp_path / "state.json")

    async def fake_available(self):
        return False

    monkeypatch.setattr(SandboxManager, "is_available", fake_available)

    result = await mgr.run(
        "python -c \"print('fallback-after-approval')\"", timeout=30
    )
    assert not result.is_error
    assert result.meta is not None
    assert result.meta["mode"] == "local"
    assert result.meta["fallback"] is True
    assert "fallback-after-approval" in result.output


@pytest.mark.asyncio
async def test_strict_mode_forces_safe_commands_to_sandbox(monkeypatch, tmp_path):
    mgr = SandboxManager(mode="strict", state_path=tmp_path / "state.json")

    async def fake_available(self):
        return True

    async def fake_run_sandbox(self, command, cwd, timeout, decision):
        return ToolResult(
            output="strict-ok",
            meta={
                "sandbox": True,
                "tier": decision.tier,
                "reason": decision.reason,
            },
        )

    monkeypatch.setattr(SandboxManager, "is_available", fake_available)
    monkeypatch.setattr(SandboxManager, "_run_sandbox", fake_run_sandbox)

    result = await mgr.run("echo hi", timeout=30)
    assert result.output == "strict-ok"
    assert result.meta is not None
    assert result.meta["reason"] == "strict_force"
    assert result.meta["tier"] == "clean"


@pytest.mark.asyncio
async def test_no_fallback_refuses_to_run_locally(monkeypatch, tmp_path):
    mgr = SandboxManager(mode="auto", state_path=tmp_path / "state.json")

    async def fake_available(self):
        return False

    monkeypatch.setattr(SandboxManager, "is_available", fake_available)

    result = await mgr.run(
        "python -c \"print('nope')\"",
        timeout=30,
        allow_fallback=False,
    )
    assert result.is_error
    assert "does not allow falling back" in result.output
    assert "nope" not in result.output


def test_state_importance_and_eviction_order(tmp_path):
    now = time.time()
    old = ProjectRecord("a", "/x/a", last_used=now - 1000, access_count=1, window_start=now - 1000)
    hot = ProjectRecord("b", "/x/b", last_used=now, access_count=9, window_start=now - 10)
    assert old.importance(now) == 1.0
    assert hot.importance(now) == 9.0
    assert hot.importance(now) > old.importance(now)

    from code_agent.sandbox.state import SandboxStateStore

    store = SandboxStateStore(tmp_path / "state.json")
    store._records = {"a": old, "b": hot}
    order = store.eviction_order()
    assert [r.project_id for r in order] == ["a", "b"]


def test_protected_project_never_evicted_first(tmp_path):
    from code_agent.sandbox.state import SandboxStateStore

    now = time.time()
    protected = ProjectRecord(
        "p", "/x/p", last_used=now - 5000, access_count=0,
        window_start=now - 5000, protected=True,
    )
    fresh = ProjectRecord(
        "f", "/x/f", last_used=now, access_count=2, window_start=now - 5,
    )
    store = SandboxStateStore(tmp_path / "state.json")
    store._records = {"p": protected, "f": fresh}

    evictable = [
        r for r in store.eviction_order() if not r.protected
    ]
    assert [r.project_id for r in evictable] == ["f"]


@pytest.mark.asyncio
async def test_gc_returns_stats_without_docker(monkeypatch, tmp_path):
    mgr = SandboxManager(mode="auto", state_path=tmp_path / "state.json")

    async def fake_available(self):
        return False

    monkeypatch.setattr(SandboxManager, "is_available", fake_available)
    stats = await mgr.gc()
    assert stats["containers_removed"] == 0
    assert stats["projects_evicted"] == 0


def test_list_projects_sorted_by_importance(tmp_path):
    from code_agent.sandbox.state import ProjectRecord, SandboxStateStore

    now = time.time()
    store = SandboxStateStore(tmp_path / "state.json")
    store._records = {
        "low": ProjectRecord("low", "/x/low", last_used=now - 100, access_count=1, window_start=now - 100),
        "high": ProjectRecord("high", "/x/high", last_used=now, access_count=5, window_start=now - 5),
    }
    mgr = SandboxManager(mode="auto", state_path=tmp_path / "state.json")
    mgr._state = store
    rows = mgr.list_projects()
    assert rows[0]["project_id"] == "high"
    assert rows[1]["project_id"] == "low"


def test_parse_size_values():
    assert _parse_size("1.5GB") == int(1.5 * 1024**3)
    assert _parse_size("512MB") == 512 * 1024**2
    assert _parse_size("10B") == 10
    assert _parse_size("2KiB") == 2048
    assert _parse_size("") == 0
    assert _parse_size("bogus") == 0


def test_evict_persistent_until_uses_real_volume_sizes(tmp_path):
    from code_agent.sandbox.state import ProjectRecord, SandboxStateStore

    now = time.time()
    store = SandboxStateStore(tmp_path / "state.json")
    store._records = {
        "a": ProjectRecord(
            "a", "/x/a", last_used=now - 100, access_count=1,
            window_start=now - 100,
        ),
        "b": ProjectRecord(
            "b", "/x/b", last_used=now - 50, access_count=2,
            window_start=now - 50,
        ),
    }
    mgr = SandboxManager(mode="auto", state_path=tmp_path / "state.json")
    mgr._state = store
    sizes = {
        _docker_volume_name("a"): 1024**3,
        _docker_volume_name("b"): 512 * 1024**2,
    }
    victims = mgr._evict_persistent_until(
        target_bytes=512 * 1024**2,
        current_bytes=1536 * 1024**2,
        sizes=sizes,
    )
    assert victims == ["a"]
