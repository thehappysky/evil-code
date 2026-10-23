"""Stress: permission decisions must not serialize parallel tool execution."""

import asyncio
import json

import pytest
from pydantic import BaseModel, Field

from code_agent.api.schemas import StreamEvent, ToolCallDelta
from code_agent.core.agent import Agent
from code_agent.core.session import Session
from code_agent.hooks.phase4_hooks import PermissionHook
from code_agent.permission.manager import PermissionManager
from code_agent.tools.registry import ToolRegistry
from code_agent.tools.result import ToolResult

from conftest import MockChatClient, make_text_response


class TestPermissionParallel:
    @pytest.mark.asyncio
    async def test_twenty_parallel_tools_with_permission_checks(self):
        """20 slow tools in one turn all pass the permission pipeline, keep
        their order, and run concurrently (not serialized by the hook)."""
        registry = ToolRegistry()

        class SlowInput(BaseModel):
            tag: str = Field(default="x")

        async def slow(input: SlowInput) -> ToolResult:
            await asyncio.sleep(0.05)
            return ToolResult.ok(f"slow-{input.tag}")

        registry.register("slow", SlowInput, description="sleepy")(slow)
        registry.finalize()

        client = MockChatClient()
        events: list[StreamEvent] = []
        for i in range(20):
            events.append(StreamEvent(
                kind="tool_call_delta",
                tool_call=ToolCallDelta(index=i, id=f"c{i}", name="slow"),
            ))
            events.append(StreamEvent(
                kind="tool_call_delta",
                tool_call=ToolCallDelta(
                    index=i, arguments_fragment=json.dumps({"tag": str(i)}),
                ),
            ))
        events.append(StreamEvent(kind="turn_end"))
        client.set_responses([events, make_text_response("done")])

        # Real decision pipeline per call (allow rule, not YOLO).
        pm = PermissionManager(allow_rules=["slow"])
        agent = Agent(
            Session(system_prompt="T"), client, registry, max_turns=5,
            hooks=[PermissionHook(pm, ask_action="deny")],
        )

        start = asyncio.get_event_loop().time()
        async for _ in agent.run("run twenty slow tools"):
            pass
        elapsed = asyncio.get_event_loop().time() - start

        tool_msgs = [
            m for m in agent._session.messages if m.role == "tool"
        ]
        assert len(tool_msgs) == 20
        assert all(not m.is_error for m in tool_msgs)  # no permission misfire
        assert [m.content for m in tool_msgs] == [
            f"slow-{i}" for i in range(20)
        ]  # model order preserved

        # Serial would be ~1.0s (20 × 0.05s); parallel is ~0.05-0.15s.
        assert elapsed < 0.5, f"tools were serialized: {elapsed:.2f}s"
