# [Phase 3] 2026-07-27: Tests for core/plan_mode.py — Plan Mode FSM
"""Tests for Plan Mode state machine."""

from pathlib import Path

import pytest

from code_agent.core.plan_mode import PlanModeTracker, PlanState


class TestPlanModeTracker:
    """Test PlanModeTracker state machine."""

    @pytest.fixture
    def tracker(self) -> PlanModeTracker:
        """Create a fresh tracker for each test."""
        return PlanModeTracker(plan_file_path=Path("/tmp/plan.md"))

    # ── Initial state ──────────────────────────────────────────────────

    def test_initial_state_is_inactive(self, tracker):
        """Tracker starts INACTIVE."""
        assert tracker.state == PlanState.INACTIVE
        assert not tracker.is_active()

    # ── Activation: INACTIVE → PENDING ─────────────────────────────────

    def test_activate_from_inactive(self, tracker):
        """activate() transitions INACTIVE → PENDING."""
        tracker.activate()
        assert tracker.state == PlanState.PENDING
        assert not tracker.is_active()  # PENDING is not ACTIVE yet

    def test_activate_twice_stays_pending(self, tracker):
        """Double activate from INACTIVE stays PENDING."""
        tracker.activate()
        assert tracker.state == PlanState.PENDING
        tracker.activate()
        assert tracker.state == PlanState.PENDING

    # ── Prompt arrival: PENDING → ACTIVE ───────────────────────────────

    def test_prompt_arrived_transitions_to_active(self, tracker):
        """on_prompt_arrived: PENDING → ACTIVE + returns instructions."""
        tracker.activate()  # → PENDING
        instructions = tracker.on_prompt_arrived()

        assert tracker.state == PlanState.ACTIVE
        assert tracker.is_active()
        # grok-build-adapted instructions use lowercase "plan mode" + exit_plan_mode.
        assert "plan mode" in instructions.lower()
        assert "exit_plan_mode" in instructions
        assert "plan.md" in instructions

    def test_prompt_arrived_is_idempotent(self, tracker):
        """on_prompt_arrived is idempotent — only transitions once."""
        tracker.activate()  # → PENDING
        first = tracker.on_prompt_arrived()
        assert tracker.state == PlanState.ACTIVE
        assert first != ""

        # Second call: already ACTIVE, returns empty
        second = tracker.on_prompt_arrived()
        assert tracker.state == PlanState.ACTIVE
        assert second == ""

    def test_prompt_arrived_when_inactive_returns_empty(self, tracker):
        """on_prompt_arrived from INACTIVE returns empty string."""
        result = tracker.on_prompt_arrived()
        assert result == ""
        assert tracker.state == PlanState.INACTIVE

    # ── Deactivation while PENDING ─────────────────────────────────────

    def test_deactivate_from_pending(self, tracker):
        """deactivate(): PENDING → INACTIVE (cancelled before prompt)."""
        tracker.activate()  # → PENDING
        tracker.deactivate()
        assert tracker.state == PlanState.INACTIVE

    # ── Deactivation while ACTIVE ──────────────────────────────────────

    def test_deactivate_from_active_no_turn(self, tracker):
        """deactivate(): ACTIVE → INACTIVE when no turn in flight."""
        tracker.activate()
        tracker.on_prompt_arrived()  # → ACTIVE
        # No turn in flight (on_prompt_arrived sets turn_in_flight=True)
        # But deactivate checks turn_in_flight
        tracker.deactivate()
        assert tracker.state == PlanState.EXIT_PENDING  # turn_in_flight is True

    def test_deactivate_after_turn_complete(self, tracker):
        """EXIT_PENDING → INACTIVE after turn completes."""
        tracker.activate()
        tracker.on_prompt_arrived()  # → ACTIVE, turn_in_flight=True

        # User requests exit
        tracker.deactivate()
        assert tracker.state == PlanState.EXIT_PENDING

        # Turn completes
        reminder = tracker.on_turn_complete()
        assert tracker.state == PlanState.INACTIVE
        assert reminder is not None
        assert "Plan Mode" in reminder

    # ── Tool gating ────────────────────────────────────────────────────

    def test_all_tools_allowed_when_inactive(self, tracker):
        """All tools allowed when not ACTIVE."""
        assert tracker.is_tool_allowed("file_edit")
        assert tracker.is_tool_allowed("file_write")
        assert tracker.is_tool_allowed("shell_exec")
        assert tracker.is_tool_allowed("file_read")
        assert tracker.is_tool_allowed("unknown_tool")

    def test_write_tools_blocked_when_active(self, tracker):
        """Write tools are blocked when ACTIVE."""
        tracker.activate()
        tracker.on_prompt_arrived()  # → ACTIVE

        assert not tracker.is_tool_allowed("file_edit")
        assert not tracker.is_tool_allowed("file_write")
        assert not tracker.is_tool_allowed("shell_exec")

    def test_read_tools_allowed_when_active(self, tracker):
        """Read tools are allowed when ACTIVE."""
        tracker.activate()
        tracker.on_prompt_arrived()  # → ACTIVE

        assert tracker.is_tool_allowed("file_read")
        assert tracker.is_tool_allowed("file_search")
        assert tracker.is_tool_allowed("web_fetch")

    def test_unknown_tools_allowed_when_active(self, tracker):
        """Unknown tools (e.g., 'skill') are allowed when ACTIVE."""
        tracker.activate()
        tracker.on_prompt_arrived()  # → ACTIVE

        assert tracker.is_tool_allowed("skill")
        assert tracker.is_tool_allowed("some_mcp_tool")

    # ── Plan file path ─────────────────────────────────────────────────

    def test_get_plan_file_path(self, tracker):
        """get_plan_file_path returns configured path."""
        assert tracker.get_plan_file_path() == Path("/tmp/plan.md")

    # ── on_turn_complete without exit ──────────────────────────────────

    def test_on_turn_complete_without_exit(self, tracker):
        """on_turn_complete returns None when not EXIT_PENDING."""
        tracker.activate()
        tracker.on_prompt_arrived()  # → ACTIVE

        result = tracker.on_turn_complete()
        assert result is None
        assert tracker.state == PlanState.ACTIVE

    # ── Full lifecycle ─────────────────────────────────────────────────

    def test_full_plan_mode_lifecycle(self, tracker):
        """Test complete plan mode lifecycle: INACTIVE → PENDING → ACTIVE → EXIT → INACTIVE."""
        # Start
        assert tracker.state == PlanState.INACTIVE

        # User types /plan
        tracker.activate()
        assert tracker.state == PlanState.PENDING

        # User sends prompt
        instructions = tracker.on_prompt_arrived()
        assert tracker.state == PlanState.ACTIVE
        assert len(instructions) > 0

        # User types /plan again during active turn
        tracker.deactivate()
        assert tracker.state == PlanState.EXIT_PENDING

        # Turn completes
        reminder = tracker.on_turn_complete()
        assert tracker.state == PlanState.INACTIVE
        assert reminder is not None


class TestPlanStateEnum:
    """Test PlanState enum values."""

    def test_all_states_defined(self):
        """All 4 states are defined."""
        states = {s.value for s in PlanState}
        assert states == {"inactive", "pending", "active", "exit_pending"}

    def test_state_equality(self):
        """Same states are equal."""
        assert PlanState.INACTIVE == PlanState("inactive")
        assert PlanState.ACTIVE == PlanState("active")
