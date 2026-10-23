"""Tests for hooks/phase3_hooks.py: PlanModeHook.

PlanModeHook adapts PlanModeTracker to the AgentHook interface so Plan Mode
integrates into the Agent loop without touching core/agent.py. The key behavior
is the tool interception: while Plan Mode is ACTIVE, write tools are DENY'd and
read tools are ALLOW'd.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_agent.core.hooks import HookDecision
from code_agent.core.plan_mode import PlanModeTracker
from code_agent.core.session import Session
from code_agent.hooks.phase3_hooks import PlanModeHook


@pytest.fixture
def tracker(tmp_path: Path) -> PlanModeTracker:
    return PlanModeTracker(tmp_path / "plan.md")


@pytest.fixture
def hook(tracker: PlanModeTracker) -> PlanModeHook:
    return PlanModeHook(tracker)


class TestToolInterception:
    async def test_inactive_allows_everything(self, hook, tracker):
        assert tracker.is_active() is False
        for tool in ("file_read", "file_edit", "file_write", "shell_exec", "echo"):
            assert await hook.on_tool_call(tool, {}) is HookDecision.ALLOW

    async def test_active_blocks_write_tools(self, hook, tracker):
        tracker.activate()
        tracker.on_prompt_arrived()  # PENDING -> ACTIVE
        assert tracker.is_active()
        for tool in ("file_edit", "file_write", "shell_exec"):
            assert await hook.on_tool_call(tool, {}) is HookDecision.DENY

    async def test_active_allows_read_tools(self, hook, tracker):
        tracker.activate()
        tracker.on_prompt_arrived()
        for tool in ("file_read", "file_search", "web_fetch"):
            assert await hook.on_tool_call(tool, {}) is HookDecision.ALLOW

    async def test_unknown_tool_allowed_while_active(self, hook, tracker):
        tracker.activate()
        tracker.on_prompt_arrived()
        assert await hook.on_tool_call("skill", {}) is HookDecision.ALLOW

    async def test_active_allows_writing_plan_file(self, hook, tracker, tmp_path):
        """Writing the plan file itself is allowed while other writes are blocked."""
        tracker.activate()
        tracker.on_prompt_arrived()
        plan = tracker.get_plan_file_path()
        # Other writes still blocked.
        assert await hook.on_tool_call("file_edit", {}) is HookDecision.DENY
        # Writing the plan file is allowed.
        assert await hook.on_tool_call(
            "file_edit", {"file_path": str(plan)},
        ) is HookDecision.ALLOW
        assert await hook.on_tool_call(
            "file_write", {"file_path": str(plan)},
        ) is HookDecision.ALLOW
        # A different file is still blocked.
        assert await hook.on_tool_call(
            "file_write", {"file_path": str(tmp_path / "other.py")},
        ) is HookDecision.DENY


class TestInstructionInjection:
    async def test_on_turn_start_injects_instructions_when_activated(self, hook, tracker):
        session = Session(system_prompt="T")
        tracker.activate()
        # First prompt arrival moves PENDING -> ACTIVE and returns instructions.
        await hook.on_turn_start(session, 0, [])
        assert tracker.is_active()
        assert any("plan" in m.content.lower() for m in session._messages)

    async def test_on_turn_end_injects_exit_reminder(self, hook, tracker):
        session = Session(system_prompt="T")
        tracker.activate()
        tracker.on_prompt_arrived()  # PENDING -> ACTIVE, turn_in_flight=True
        tracker.deactivate()         # ACTIVE + turn in flight -> EXIT_PENDING
        await hook.on_turn_end(session, 1)  # EXIT_PENDING -> INACTIVE + reminder
        assert tracker.is_active() is False
        assert any(m.content for m in session._messages)


class TestExceptionSafety:
    async def test_on_tool_call_exception_treated_as_deny(self, tmp_path, monkeypatch):
        tracker = PlanModeTracker(tmp_path / "plan.md")
        tracker.activate()

        def _boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(tracker, "is_active", _boom)
        hook = PlanModeHook(tracker)
        assert await hook.on_tool_call("file_edit", {}) is HookDecision.DENY

    async def test_on_turn_start_exception_degrades_gracefully(self, tmp_path, monkeypatch):
        tracker = PlanModeTracker(tmp_path / "plan.md")
        session = Session(system_prompt="T")

        def _boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(tracker, "on_prompt_arrived", _boom)
        hook = PlanModeHook(tracker)
        # Must not raise.
        await hook.on_turn_start(session, 0, [])
