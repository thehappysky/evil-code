# [Phase 3] 2026-07-27: Headless mode — JSON and streaming-json output formats
"""Headless mode with structured output formats.

References:
    - design-docs/Python-Agent-MVP-Phase3-详细设计.md §9 cli/headless.py
    - Rust headless mode (--output-format)

Three output modes:
    1. plain: default, streams text_delta to stdout
    2. json: single JSON object with full response after completion
    3. streaming-json: NDJSON (one JSON object per line) during streaming
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from code_agent.core.agent import Agent


async def run_headless_plain(agent: "Agent", prompt: str) -> str:
    """Headless plain-text output.

    Streams text_delta to stdout as it arrives.

    Args:
        agent: The Agent instance.
        prompt: The user prompt.

    Returns:
        The complete agent response as a string.
    """
    full_text: list[str] = []

    async for event in agent.run(prompt):
        if event.kind == "text_delta" and event.text:
            full_text.append(event.text)
            print(event.text, end="", flush=True)
        elif event.kind == "error" and event.text:
            print(f"\n[Error] {event.text}", file=sys.stderr)

    print()  # Final newline
    return "".join(full_text)


async def run_headless_json(agent: "Agent", prompt: str) -> None:
    """Headless JSON output.

    Outputs a single JSON object to stdout after the response completes.

    Output format::

        {
          "text": "Agent full response text",
          "stopReason": "EndTurn",
          "sessionId": "abc123",
          "num_turns": 7,
          "usage": {
            "input_tokens": 7210,
            "output_tokens": 1893
          }
        }
    """
    full_text: list[str] = []
    num_turns: int = 0
    stop_reason: str = "EndTurn"

    async for event in agent.run(prompt):
        if event.kind == "text_delta" and event.text:
            full_text.append(event.text)
        elif event.kind == "tool_call_delta" and event.tool_call:
            num_turns += 1
        elif event.kind == "turn_end":
            num_turns += 1
        elif event.kind == "error" and event.text:
            stop_reason = event.text

    # Determine session ID + real usage from the agent's session ledger
    session_id, usage = _session_usage(agent)

    result = {
        "text": "".join(full_text),
        "stopReason": stop_reason,
        "sessionId": session_id,
        "num_turns": num_turns,
        "usage": usage,
    }
    print(json.dumps(result, ensure_ascii=False))


async def run_headless_streaming(agent: "Agent", prompt: str) -> None:
    """Headless streaming-json output (NDJSON).

    Each line is a self-contained JSON object.

    Output format::

        {"type":"text","data":"Here is"}
        {"type":"text","data":" the answer"}
        {"type":"end","stopReason":"EndTurn","sessionId":"abc123","usage":{}}
    """
    num_turns: int = 0
    stop_reason: str = "EndTurn"

    async for event in agent.run(prompt):
        if event.kind == "text_delta" and event.text:
            line = json.dumps(
                {"type": "text", "data": event.text},
                ensure_ascii=False,
            )
            print(line, flush=True)

        elif event.kind == "tool_call_delta" and event.tool_call:
            if event.tool_call.name:  # Only emit on first delta (name present)
                line = json.dumps(
                    {
                        "type": "tool_call",
                        "name": event.tool_call.name,
                    },
                    ensure_ascii=False,
                )
                print(line, flush=True)

        elif event.kind == "turn_end":
            num_turns += 1

        elif event.kind == "error" and event.text:
            stop_reason = event.text
            line = json.dumps(
                {"type": "error", "data": event.text},
                ensure_ascii=False,
            )
            print(line, flush=True)

    # Determine session ID + real usage from the agent's session ledger
    session_id, usage = _session_usage(agent)

    # Emit final end event
    end_line = json.dumps(
        {
            "type": "end",
            "stopReason": stop_reason,
            "sessionId": session_id,
            "usage": usage,
        },
        ensure_ascii=False,
    )
    print(end_line, flush=True)


def _session_usage(agent) -> tuple[str, dict]:
    """Read the session id and real usage totals from an Agent's session.

    Returns:
        (session_id, usage_dict). Empty strings/zeros if unavailable.
    """
    session_id = ""
    usage: dict = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "model_calls": 0,
    }
    try:
        session = agent._session
        sid = getattr(session, "_session_id", None)
        if sid:
            session_id = sid
        totals = session.usage_totals
        usage = totals.to_dict()
    except Exception:
        pass
    return session_id, usage
