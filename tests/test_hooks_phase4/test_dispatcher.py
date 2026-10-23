# [Phase 4] 2026-07-29: Tests for hooks/dispatcher.py — HookDispatcher
"""Tests for HookDispatcher."""

import pytest

from code_agent.hooks.dispatcher import HookDispatcher, HookEvent, HookResult


@pytest.fixture
def dispatcher() -> HookDispatcher:
    """Create a fresh dispatcher."""
    return HookDispatcher()


class TestHookDispatcher:
    """Test HookDispatcher registration and dispatch."""

    @pytest.mark.asyncio
    async def test_dispatch_no_handlers_returns_allow(self, dispatcher):
        """Dispatch with no handlers returns ALLOW."""
        result = await dispatcher.dispatch(HookEvent.PRETOOL_USE, {})
        assert result.action == "allow"

    @pytest.mark.asyncio
    async def test_register_and_dispatch_handler(self, dispatcher):
        """Registered handler is called on dispatch."""
        called = []

        async def my_handler(event, context):
            called.append(1)
            return HookResult(action="allow")

        dispatcher.register(HookEvent.PRETOOL_USE, my_handler)
        result = await dispatcher.dispatch(
            HookEvent.PRETOOL_USE, {"tool": "test"},
        )
        assert len(called) == 1
        assert result.action == "allow"

    @pytest.mark.asyncio
    async def test_pretool_use_deny_stops_chain(self, dispatcher):
        """PreToolUse: first deny stops the handler chain."""
        call_order = []

        async def handler1(event, context):
            call_order.append(1)
            return HookResult(action="deny", message="Blocked")

        async def handler2(event, context):
            call_order.append(2)
            return HookResult(action="allow")

        dispatcher.register(HookEvent.PRETOOL_USE, handler1)
        dispatcher.register(HookEvent.PRETOOL_USE, handler2)

        result = await dispatcher.dispatch(HookEvent.PRETOOL_USE, {})
        assert result.action == "deny"
        assert call_order == [1]  # Second handler never called

    @pytest.mark.asyncio
    async def test_handler_exception_does_not_crash(self, dispatcher):
        """Handler exceptions are caught and logged, not propagated."""
        async def bad_handler(event, context):
            raise RuntimeError("test error")

        dispatcher.register(HookEvent.TURN_START, bad_handler)

        # Should not raise
        result = await dispatcher.dispatch(HookEvent.TURN_START, {})
        assert result.action == "allow"

    @pytest.mark.asyncio
    async def test_multiple_handlers_called_in_order(self, dispatcher):
        """Handlers are called in registration order."""
        call_order = []

        async def h1(event, context):
            call_order.append("h1")
            return HookResult(action="allow")

        async def h2(event, context):
            call_order.append("h2")
            return HookResult(action="allow")

        dispatcher.register(HookEvent.TURN_START, h1)
        dispatcher.register(HookEvent.TURN_START, h2)

        await dispatcher.dispatch(HookEvent.TURN_START, {})
        assert call_order == ["h1", "h2"]

    @pytest.mark.asyncio
    async def test_messages_collected(self, dispatcher):
        """Multiple handler messages are concatenated."""
        async def h1(event, context):
            return HookResult(action="allow", message="msg1")

        async def h2(event, context):
            return HookResult(action="allow", message="msg2")

        dispatcher.register(HookEvent.POST_TOOL_USE, h1)
        dispatcher.register(HookEvent.POST_TOOL_USE, h2)

        result = await dispatcher.dispatch(HookEvent.POST_TOOL_USE, {})
        assert "msg1" in result.message
        assert "msg2" in result.message

    @pytest.mark.asyncio
    async def test_different_events_independent(self, dispatcher):
        """Handlers for different events are independent."""
        called_a = []
        called_b = []

        async def ha(event, context):
            called_a.append(1)
            return HookResult(action="allow")

        async def hb(event, context):
            called_b.append(1)
            return HookResult(action="allow")

        dispatcher.register(HookEvent.TURN_START, ha)
        dispatcher.register(HookEvent.TURN_END, hb)

        await dispatcher.dispatch(HookEvent.TURN_START, {})
        assert len(called_a) == 1
        assert len(called_b) == 0

        await dispatcher.dispatch(HookEvent.TURN_END, {})
        assert len(called_a) == 1
        assert len(called_b) == 1


class TestHookEvent:
    """Test HookEvent enum."""

    def test_all_events_defined(self):
        """All 7 events are defined."""
        events = {e.value for e in HookEvent}
        expected = {
            "pre_tool_use", "post_tool_use", "post_tool_use_failure",
            "session_start", "session_end", "turn_start", "turn_end",
        }
        assert events == expected
