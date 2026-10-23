"""Tests for core/agent.py: Agent main loop + hook pipeline.

Covers:
- Agent.run() single-turn text response
- Agent.run() multi-turn tool call chain
- Agent.run() MaxTurnsReached
- Hook pipeline: on_tool_call DENY / MODIFY
- Hook exception defense (Layer 2 fallback)
"""

from __future__ import annotations

import pytest

from code_agent.api.schemas import StreamEvent, ToolCallDelta
from code_agent.core.agent import Agent
from code_agent.core.hooks import AgentHook, HookDecision
from code_agent.core.exceptions import MaxTurnsReached
from code_agent.core.session import Session
from code_agent.tools.registry import ToolRegistry
from code_agent.tools.result import ToolResult

from conftest import MockChatClient, make_text_response, make_tool_call_response


# ─── Helpers ───────────────────────────────────────────────────────────────────


def _build_agent(
    responses: list[list[StreamEvent]],
    hooks: list | None = None,
    max_turns: int = 20,
) -> tuple[Agent, MockChatClient]:
    """Build an Agent with a MockChatClient pre-loaded with responses."""
    from pydantic import BaseModel, Field

    client = MockChatClient()
    client.set_responses(responses)

    session = Session(system_prompt="Test agent.")

    registry = ToolRegistry()

    class EchoInput(BaseModel):
        text: str = Field(default="")

    @registry.register("echo", EchoInput, description="Echo tool")
    async def echo(input: EchoInput) -> ToolResult:
        return ToolResult.ok(f"Echo: {input.text}")

    registry.finalize()

    agent = Agent(
        session=session,
        client=client,
        tools=registry,
        max_turns=max_turns,
        hooks=hooks,
    )
    return agent, client


async def _collect_events(agent: Agent, user_input: str) -> list[StreamEvent]:
    """Run agent and collect all yielded events."""
    events = []
    async for event in agent.run(user_input):
        events.append(event)
    return events


# ─── Parallel tool execution (Phase 5) ─────────────────────────────────────────


class TestParallelToolExecution:
    def test_tool_lock_key_mapping(self):
        from code_agent.core.agent import _tool_lock_key
        from code_agent.core.messages import ToolCallItem

        edit_a = ToolCallItem(id="1", name="file_edit",
                              arguments='{"file_path": "a.txt"}')
        edit_a2 = ToolCallItem(id="2", name="file_edit",
                               arguments='{"file_path": "./a.txt"}')
        edit_b = ToolCallItem(id="3", name="file_edit",
                              arguments='{"file_path": "b.txt"}')
        read = ToolCallItem(id="4", name="file_read",
                            arguments='{"file_path": "a.txt"}')
        assert _tool_lock_key(edit_a) == _tool_lock_key(edit_a2)  # same path
        assert _tool_lock_key(edit_a) != _tool_lock_key(edit_b)
        assert _tool_lock_key(read) is None  # read tools not locked
        assert _tool_lock_key(ToolCallItem(id="5", name="file_edit",
                                           arguments="not json")) is None

    async def test_multiple_tool_calls_run_and_preserve_order(self):
        """Two tool calls in one turn both run; results land in model order."""
        responses = [
            [
                StreamEvent(kind="tool_call_delta", tool_call=ToolCallDelta(index=0, id="c1", name="echo")),
                StreamEvent(kind="tool_call_delta", tool_call=ToolCallDelta(index=0, arguments_fragment='{"text":"first"}')),
                StreamEvent(kind="tool_call_delta", tool_call=ToolCallDelta(index=1, id="c2", name="echo")),
                StreamEvent(kind="tool_call_delta", tool_call=ToolCallDelta(index=1, arguments_fragment='{"text":"second"}')),
                StreamEvent(kind="turn_end"),
            ],
            make_text_response("done"),
        ]
        agent, _ = _build_agent(responses)
        await _collect_events(agent, "run two")

        results = [m for m in agent._session.messages if m.role == "tool"]
        assert [m.content for m in results] == ["Echo: first", "Echo: second"]

    async def test_same_path_edits_serialized(self, tmp_path):
        """Same-path file_edit calls in one turn are serialized by the lock."""
        import json as _json

        from code_agent.cli.assembly import build_tools

        target = tmp_path / "f.txt"
        target.write_text("aaa bbb ccc", encoding="utf-8")

        registry = build_tools()
        client = MockChatClient()
        responses = [
            [
                StreamEvent(kind="tool_call_delta", tool_call=ToolCallDelta(index=0, id="c1", name="file_edit")),
                StreamEvent(kind="tool_call_delta", tool_call=ToolCallDelta(index=0, arguments_fragment=_json.dumps({"file_path": str(target), "old_string": "aaa", "new_string": "AAA"}))),
                StreamEvent(kind="tool_call_delta", tool_call=ToolCallDelta(index=1, id="c2", name="file_edit")),
                StreamEvent(kind="tool_call_delta", tool_call=ToolCallDelta(index=1, arguments_fragment=_json.dumps({"file_path": str(target), "old_string": "bbb", "new_string": "BBB"}))),
                StreamEvent(kind="turn_end"),
            ],
            make_text_response("done"),
        ]
        client.set_responses(responses)
        agent = Agent(session=Session(system_prompt="T"), client=client,
                      tools=registry, max_turns=10)
        async for _ in agent.run("edit two things"):
            pass

        # Both edits applied (would race/lose one without the per-path lock).
        assert target.read_text(encoding="utf-8") == "AAA BBB ccc"


# ─── Session-end hook (Phase 5) ────────────────────────────────────────────────


class TestSessionEndHook:
    async def test_on_session_end_fires_on_completion(self):
        recorded = []

        class EndHook(AgentHook):
            async def on_session_end(self, session, reason="completed"):
                recorded.append(reason)

        agent, _ = _build_agent([make_text_response("hi")], hooks=[EndHook()])
        await _collect_events(agent, "hello")
        assert recorded == ["completed"]

    async def test_on_session_end_fires_on_max_turns(self):
        recorded = []

        class EndHook(AgentHook):
            async def on_session_end(self, session, reason="completed"):
                recorded.append(reason)

        agent, _ = _build_agent(
            [make_text_response("hi")], hooks=[EndHook()], max_turns=0,
        )
        with pytest.raises(MaxTurnsReached):
            await _collect_events(agent, "hello")
        assert recorded == ["max_turns"]

    async def test_on_session_end_fires_exactly_once(self):
        recorded = []

        class EndHook(AgentHook):
            async def on_session_end(self, session, reason="completed"):
                recorded.append(reason)

        agent, _ = _build_agent([make_text_response("hi")], hooks=[EndHook()])
        await _collect_events(agent, "hello")
        await _collect_events(agent, "again")
        # Each run fires its own session end, exactly once per run.
        assert recorded == ["completed", "completed"]


# ─── Usage accounting ──────────────────────────────────────────────────────────


class TestAgentUsageAccounting:
    async def test_turn_end_usage_recorded_into_session(self):
        """Real usage carried by the turn_end event is recorded in the ledger."""
        response = [
            StreamEvent(kind="text_delta", text="Answer"),
            StreamEvent(
                kind="turn_end",
                usage={
                    "prompt_tokens": 120,
                    "completion_tokens": 30,
                    "cached_prompt_tokens": 50,
                    "reasoning_tokens": 5,
                },
            ),
        ]
        agent, _ = _build_agent([response])
        await _collect_events(agent, "hi")

        totals = agent._session.usage_totals
        assert totals.input_tokens == 120
        assert totals.output_tokens == 30
        assert totals.cached_input_tokens == 50
        assert totals.reasoning_tokens == 5
        assert totals.model_calls == 1
        assert totals.by_model["test-model"]["total_tokens"] == 150

    async def test_no_usage_no_record(self):
        """A turn_end without usage does not add a model call."""
        agent, _ = _build_agent([make_text_response("hi")])
        await _collect_events(agent, "hello")
        assert agent._session.usage_totals.model_calls == 0


# ─── Single-turn text response ─────────────────────────────────────────────────


class TestAgentTextResponse:
    async def test_single_turn_text(self):
        """Agent yields text_delta + turn_end for a simple response."""
        agent, _ = _build_agent([make_text_response("Hello world!")])

        events = await _collect_events(agent, "Hi")

        kinds = [e.kind for e in events]
        assert "text_delta" in kinds
        assert "turn_end" in kinds

        text_parts = [e.text for e in events if e.kind == "text_delta"]
        assert "".join(text_parts) == "Hello world!"

    async def test_session_updated_after_text(self):
        """Session contains user + assistant messages after text response."""
        agent, _ = _build_agent([make_text_response("Answer")])
        await _collect_events(agent, "Question")

        # Access session via agent internals
        msgs = agent._session.to_api_messages()
        roles = [m["role"] for m in msgs]
        assert "user" in roles
        assert "assistant" in roles


# ─── Multi-turn tool call chain ────────────────────────────────────────────────


class TestAgentToolCalls:
    async def test_single_tool_call(self):
        """Agent executes tool call and continues to next turn."""
        responses = [
            # Turn 1: model calls echo tool
            make_tool_call_response("call_1", "echo", '{"text": "hello"}'),
            # Turn 2: model returns text after seeing tool result
            make_text_response("Tool said hello"),
        ]
        agent, client = _build_agent(responses)

        events = await _collect_events(agent, "Call echo")

        # Should have tool_call_delta events from turn 1
        tc_events = [e for e in events if e.kind == "tool_call_delta"]
        assert len(tc_events) > 0

        # Should have text from turn 2
        text_events = [e for e in events if e.kind == "text_delta"]
        assert any("Tool said hello" in (e.text or "") for e in text_events)

        # Client was called twice (two turns)
        assert len(client.call_history) == 2

    async def test_tool_result_in_session(self):
        """Tool result is stored in session for next turn's context."""
        responses = [
            make_tool_call_response("call_1", "echo", '{"text": "test"}'),
            make_text_response("Done"),
        ]
        agent, client = _build_agent(responses)
        await _collect_events(agent, "Run echo")

        # Second call's messages should include tool result
        second_call_msgs = client.call_history[1]["messages"]
        tool_msgs = [m for m in second_call_msgs if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "Echo: test" in tool_msgs[0]["content"]

    async def test_invalid_json_arguments(self):
        """Tool call with invalid JSON arguments returns error result."""
        responses = [
            make_tool_call_response("call_1", "echo", "not valid json{{{"),
            make_text_response("Recovered"),
        ]
        agent, client = _build_agent(responses)
        events = await _collect_events(agent, "Bad args")

        # Should still complete (error fed back to model)
        text_events = [e for e in events if e.kind == "text_delta"]
        assert any("Recovered" in (e.text or "") for e in text_events)


# ─── MaxTurnsReached ───────────────────────────────────────────────────────────


class TestMaxTurns:
    async def test_max_turns_reached(self):
        """Agent raises MaxTurnsReached when turns exhausted."""
        # Every turn calls a tool — never produces text-only response
        tool_response = make_tool_call_response("call_x", "echo", '{"text": "loop"}')
        responses = [tool_response] * 5  # More than max_turns

        agent, _ = _build_agent(responses, max_turns=3)

        with pytest.raises(MaxTurnsReached):
            await _collect_events(agent, "Loop forever")


# ─── Hook pipeline ─────────────────────────────────────────────────────────────


class DenyHook:
    """Hook that denies all tool calls."""

    async def on_session_start(self, session):
        pass

    async def on_turn_start(self, session, turn, tools):
        pass

    async def on_tool_call(self, tool_name: str, arguments: dict) -> HookDecision:
        return HookDecision.DENY

    async def on_tool_result(self, tool_name, arguments, result):
        pass

    async def on_turn_end(self, session, turn):
        pass


class ExplodingHook:
    """Hook that raises exceptions everywhere (tests Layer 2 defense)."""

    async def on_session_start(self, session):
        raise RuntimeError("boom session_start")

    async def on_turn_start(self, session, turn, tools):
        raise RuntimeError("boom turn_start")

    async def on_tool_call(self, tool_name: str, arguments: dict) -> HookDecision:
        raise RuntimeError("boom tool_call")

    async def on_tool_result(self, tool_name, arguments, result):
        raise RuntimeError("boom tool_result")

    async def on_turn_end(self, session, turn):
        raise RuntimeError("boom turn_end")


class TestHookPipeline:
    async def test_deny_hook_blocks_tool(self):
        """DENY hook prevents tool execution, returns error to model."""
        responses = [
            make_tool_call_response("call_1", "echo", '{"text": "blocked"}'),
            make_text_response("Denied"),
        ]
        agent, client = _build_agent(responses, hooks=[DenyHook()])
        events = await _collect_events(agent, "Try echo")

        # Tool was denied — model gets error result and responds with text
        second_msgs = client.call_history[1]["messages"]
        tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
        assert "denied" in tool_msgs[0]["content"].lower()

    async def test_exploding_hook_layer2_defense(self):
        """Agent survives hooks that throw exceptions (Layer 2 defense)."""
        responses = [
            make_tool_call_response("call_1", "echo", '{"text": "safe"}'),
            make_text_response("Survived"),
        ]
        agent, _ = _build_agent(responses, hooks=[ExplodingHook()])

        # Should NOT raise — Agent catches hook exceptions
        events = await _collect_events(agent, "Dangerous hooks")

        text_events = [e for e in events if e.kind == "text_delta"]
        assert any("Survived" in (e.text or "") for e in text_events)

    async def test_exploding_on_tool_call_treated_as_deny(self):
        """Hook exception in on_tool_call is treated as DENY (fail-safe)."""
        responses = [
            make_tool_call_response("call_1", "echo", '{"text": "x"}'),
            make_text_response("After deny"),
        ]
        agent, client = _build_agent(responses, hooks=[ExplodingHook()])
        await _collect_events(agent, "Test")

        # on_tool_call exception -> DENY -> tool not executed
        second_msgs = client.call_history[1]["messages"]
        tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
        assert "denied" in tool_msgs[0]["content"].lower()
