# [Phase 7] 2026-08-04: Tests for the sub-agent coordinator + task tool
"""Tests for code_agent/subagents/coordinator.py and tools/builtin/task.py."""

from __future__ import annotations

import asyncio

import pytest

from code_agent.subagents.coordinator import SubagentCoordinator
from code_agent.tools.builtin.task import TaskInput, TaskTool


class TestSubagentCoordinator:
    async def test_spawn_calls_spawn_fn_and_returns_result(self):
        calls = []

        async def spawn_fn(prompt, subagent_type, capability_mode):
            calls.append((prompt, subagent_type, capability_mode))
            return "sub answer"

        coord = SubagentCoordinator(spawn_fn, max_depth=1)
        result = await coord.spawn("do it", "explore", "read-only")

        assert result == "sub answer"
        assert calls == [("do it", "explore", "read-only")]
        assert coord.depth == 0

    async def test_depth_limit_returns_error(self):
        calls = []

        async def spawn_fn(prompt, subagent_type, capability_mode):
            calls.append(prompt)
            return "answer"

        coord = SubagentCoordinator(spawn_fn, max_depth=1)
        # First spawn is allowed.
        assert (await coord.spawn("a")) == "answer"
        # Simulate a sub-agent being mid-spawn: raise depth to the limit.
        coord._depth = 1
        refused = await coord.spawn("b")
        assert "depth limit" in refused
        assert calls == ["a"]  # "b" never reached spawn_fn

    async def test_spawn_fn_exception_returns_error(self):
        async def boom(prompt, subagent_type, capability_mode):
            raise RuntimeError("crash")

        coord = SubagentCoordinator(boom, max_depth=1)
        result = await coord.spawn("x")
        assert "crash" in result

    async def test_spawn_runs_a_real_agent(self):
        """The coordinator drives a real Agent and collects its text."""
        from code_agent.core.agent import Agent
        from code_agent.core.session import Session
        from code_agent.tools.registry import ToolRegistry

        from conftest import MockChatClient, make_text_response

        async def spawn_fn(prompt, subagent_type, capability_mode):
            client = MockChatClient()
            client.set_responses([make_text_response("sub result")])
            session = Session(system_prompt="sub-agent")
            registry = ToolRegistry()
            registry.finalize()
            agent = Agent(session, client, registry, max_turns=5)
            parts = []
            async for event in agent.run(prompt):
                if event.kind == "text_delta" and event.text:
                    parts.append(event.text)
            return "".join(parts)

        coord = SubagentCoordinator(spawn_fn, max_depth=1)
        result = await coord.spawn("do it")
        assert result == "sub result"
        assert coord.depth == 0


class TestTaskTool:
    async def test_run_calls_spawn_and_wraps_result(self):
        async def spawn(prompt, subagent_type, capability_mode):
            return "collected answer"

        tool = TaskTool(spawn)
        result = await tool.run(TaskInput(
            prompt="find the bug", subagent_type="explore", capability_mode="read-only",
        ))
        assert "collected answer" in result
        assert "<subagent_result" in result
        assert "explore" in result
