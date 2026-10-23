"""Tests for cli/headless.py structured output modes."""

from __future__ import annotations

import json

import pytest

from code_agent.cli.headless import (
    run_headless_json,
    run_headless_plain,
    run_headless_streaming,
)
from code_agent.core.agent import Agent
from code_agent.core.session import Session
from code_agent.tools.registry import ToolRegistry

from conftest import MockChatClient, make_text_response, make_tool_call_response


def _build_agent(responses: list[list]) -> Agent:
    client = MockChatClient()
    client.set_responses(responses)
    return Agent(
        session=Session(system_prompt="Test"),
        client=client,
        tools=ToolRegistry(),
        max_turns=10,
    )


class TestPlain:
    async def test_streams_text_and_returns_full(self, capsys):
        agent = _build_agent([make_text_response("Hello world")])
        result = await run_headless_plain(agent, "hi")
        captured = capsys.readouterr()
        assert result == "Hello world"
        assert "Hello world" in captured.out

    async def test_error_event_goes_to_stderr(self, capsys):
        agent = _build_agent([[make_text_response("partial")]])
        # Inject an error event by using a custom client sequence.
        from code_agent.api.schemas import StreamEvent

        client = MockChatClient()
        client.set_responses([
            [StreamEvent(kind="text_delta", text="partial"),
             StreamEvent(kind="error", text="boom"),
             StreamEvent(kind="turn_end")],
        ])
        agent = Agent(session=Session(system_prompt="T"), client=client, tools=ToolRegistry(), max_turns=5)
        result = await run_headless_plain(agent, "go")
        err = capsys.readouterr().err
        assert result == "partial"
        assert "[Error] boom" in err


class TestJson:
    async def test_single_json_object(self, capsys):
        agent = _build_agent([make_text_response("Answer")])
        await run_headless_json(agent, "q")
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["text"] == "Answer"
        assert payload["stopReason"] == "EndTurn"
        assert payload["num_turns"] >= 1
        assert "usage" in payload

    async def test_tool_turn_counts_turns(self, capsys):
        agent = _build_agent([
            make_tool_call_response("c1", "echo", '{"text": "x"}'),
            make_text_response("Done"),
        ])
        await run_headless_json(agent, "q")
        payload = json.loads(capsys.readouterr().out)
        assert payload["num_turns"] >= 2


class TestStreaming:
    async def test_ndjson_lines(self, capsys):
        agent = _build_agent([make_text_response("Hello world")])
        await run_headless_streaming(agent, "q")
        lines = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
        assert any(l["type"] == "text" and l["data"] == "Hello world" for l in lines)
        end = lines[-1]
        assert end["type"] == "end"
        assert end["stopReason"] == "EndTurn"

    async def test_tool_call_line(self, capsys):
        agent = _build_agent([make_tool_call_response("c1", "echo", "{}")])
        await run_headless_streaming(agent, "q")
        lines = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
        assert any(l["type"] == "tool_call" and l["name"] == "echo" for l in lines)
        assert lines[-1]["type"] == "end"
