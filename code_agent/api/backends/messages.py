"""Anthropic Messages API backend.

Wire format: POST {base_url}/messages
Applicable to: Anthropic native API, DeepSeek Anthropic-compatible endpoint,
and any Anthropic Messages API compatible provider.

Key differences from Chat Completions:
- system message extracted to top-level "system" field (not in messages array)
- tool results go in role=user messages as tool_result content blocks
- tool calls arrive as tool_use content blocks
- streaming uses event-typed SSE: content_block_start/delta/stop,
  message_start/delta/stop
- tool call arguments stream as input_json_delta (partial_json fragments)
- block index != tool index (blocks include text+thinking+tool_use)
- requires anthropic-version header (2023-06-01)
- auth: x-api-key header (ANTHROPIC_API_KEY) or Bearer (ANTHROPIC_AUTH_TOKEN)

Message conversion: Session.to_api_messages() outputs OpenAI format.
This backend converts to Anthropic wire format in build_request().

Logic reference: Rust version stream_messages() (stream/messages.rs)
+ build_messages_request() (conversation.rs:2973).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from code_agent.api.backends.base import BackendRequest, normalize_usage
from code_agent.api.schemas import (
    AuthScheme,
    ProviderConfig,
    StreamEvent,
    ToolCallDelta,
)

logger = logging.getLogger(__name__)

_ENDPOINT = "messages"
_ANTHROPIC_VERSION = "2023-06-01"

# Anthropic stop_reason → unified mapping
_STOP_REASON_MAP = {
    "end_turn": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "stop_sequence": "stop",
    "refusal": "stop",
}


class MessagesBackend:
    """Anthropic Messages API backend.

    Stateless — safe to use as singleton.
    """

    def build_request(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        config: ProviderConfig,
    ) -> BackendRequest:
        """Construct HTTP request for /messages endpoint.

        Converts OpenAI-format messages to Anthropic wire format:
        - Extract system messages to top-level "system" field
        - Convert role=tool to role=user with tool_result content blocks
        - Convert tool_calls to tool_use content blocks
        - Convert tool definitions to Anthropic format (input_schema)
        """
        url = f"{config.base_url.rstrip('/')}/{_ENDPOINT}"

        # Auth header: XApiKey for Anthropic native, Bearer for compatible
        if config.auth_scheme == AuthScheme.X_API_KEY:
            headers = {
                "Content-Type": "application/json",
                "x-api-key": config.api_key or "",
                "anthropic-version": _ANTHROPIC_VERSION,
            }
        else:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.api_key}",
                "anthropic-version": _ANTHROPIC_VERSION,
            }

        system, converted_messages = self._convert_messages(messages)
        converted_tools = self._convert_tools(tools)

        body: dict = {
            "model": config.model,
            "messages": converted_messages,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "stream": True,
        }
        if system:
            body["system"] = system
        if converted_tools:
            body["tools"] = converted_tools
            body["tool_choice"] = {"type": "auto"}

        return BackendRequest(url=url, headers=headers, body=body)

    def _convert_messages(
        self, api_messages: list[dict]
    ) -> tuple[str | None, list[dict]]:
        """Convert OpenAI-format messages to Anthropic Messages format.

        Returns (system_prompt, messages).

        Conversion rules:
        1. role=system -> extract to top-level system string (multiple joined)
        2. role=user -> {role: "user", content: "..."}
        3. role=assistant -> content blocks: [{type: "text", text}] +
           [{type: "tool_use", id, name, input}] per tool_call
        4. role=tool -> accumulate as tool_result blocks in a pending
           user message (Anthropic puts tool results under role=user).
           Consecutive tool messages merge into one user message.
        """
        system_parts: list[str] = []
        messages: list[dict] = []
        pending_tool_results: list[dict] = []

        for msg in api_messages:
            role = msg["role"]

            if role == "system":
                self._flush_tool_results(pending_tool_results, messages)
                system_parts.append(msg.get("content", ""))

            elif role == "user":
                self._flush_tool_results(pending_tool_results, messages)
                messages.append({
                    "role": "user",
                    "content": msg.get("content", ""),
                })

            elif role == "assistant":
                self._flush_tool_results(pending_tool_results, messages)
                content: list[dict] = []
                if msg.get("content"):
                    content.append({"type": "text", "text": msg["content"]})
                for tc in msg.get("tool_calls", []):
                    func = tc.get("function", {})
                    try:
                        input_val = json.loads(func.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        input_val = {}
                    content.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "input": input_val,
                    })
                if content:
                    messages.append({"role": "assistant", "content": content})

            elif role == "tool":
                pending_tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                })

        self._flush_tool_results(pending_tool_results, messages)

        system = "\n\n".join(system_parts) if system_parts else None
        return system, messages

    @staticmethod
    def _flush_tool_results(
        pending: list[dict], messages: list[dict]
    ) -> None:
        """Flush accumulated tool results as a single user message."""
        if not pending:
            return
        # Copy the list — pending.clear() below would empty the same reference
        messages.append({"role": "user", "content": list(pending)})
        pending.clear()

    @staticmethod
    def _convert_tools(tools: list[dict] | None) -> list[dict] | None:
        """Convert OpenAI tool definitions to Anthropic format.

        OpenAI: {type: "function", function: {name, description, parameters}}
        Anthropic: {name, description, input_schema}
        """
        if not tools:
            return None
        result: list[dict] = []
        for tool in tools:
            func = tool.get("function", {})
            result.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object"}),
            })
        return result

    async def parse_stream(
        self,
        sse_events: AsyncIterator[dict],
    ) -> AsyncIterator[StreamEvent]:
        """Parse Anthropic Messages SSE events into StreamEvent.

        Anthropic uses event-typed SSE:
        - message_start: initial usage (input tokens)
        - content_block_start: opens a text/tool_use/thinking block
        - content_block_delta: streaming fragments (text/input_json/thinking)
        - content_block_stop: finalizes a block
        - message_delta: stop_reason + output token count
        - message_stop: stream end
        - error: server-side error

        Block index != tool index: blocks include all types (text, thinking,
        tool_use). We maintain a block_to_tool map, assigning sequential
        tool indices when tool_use blocks start.
        """
        block_state: dict[int, dict] = {}
        block_to_tool: dict[int, int] = {}
        next_tool_index = 0
        turn_end_yielded = False

        async for event in sse_events:
            event_type = event.get("_event", "")
            data = {k: v for k, v in event.items() if k != "_event"}

            if event_type == "message_start":
                # Initial message info — no StreamEvent yet
                pass

            elif event_type == "content_block_start":
                idx = data.get("index", 0)
                block = data.get("content_block", {})
                block_type = block.get("type", "")
                block_state[idx] = {
                    "type": block_type,
                    "text": "",
                    "tool_id": block.get("id", ""),
                    "tool_name": block.get("name", ""),
                    "args": "",
                }
                if block_type == "tool_use":
                    tool_idx = next_tool_index
                    next_tool_index += 1
                    block_to_tool[idx] = tool_idx
                    yield StreamEvent(
                        kind="tool_call_delta",
                        tool_call=ToolCallDelta(
                            index=tool_idx,
                            id=block.get("id"),
                            name=block.get("name"),
                        ),
                    )

            elif event_type == "content_block_delta":
                idx = data.get("index", 0)
                delta = data.get("delta", {})
                delta_type = delta.get("type", "")
                state = block_state.get(idx, {})

                if delta_type == "text_delta":
                    text = delta.get("text", "")
                    if state:
                        state["text"] += text
                    yield StreamEvent(kind="text_delta", text=text)

                elif delta_type == "input_json_delta":
                    partial = delta.get("partial_json", "")
                    if state:
                        state["args"] += partial
                    tool_idx = block_to_tool.get(idx, 0)
                    yield StreamEvent(
                        kind="tool_call_delta",
                        tool_call=ToolCallDelta(
                            index=tool_idx,
                            arguments_fragment=partial,
                        ),
                    )

                elif delta_type == "thinking_delta":
                    yield StreamEvent(
                        kind="thinking_delta",
                        text=delta.get("thinking", ""),
                    )

            elif event_type == "content_block_stop":
                # Block finalized — content already yielded via deltas
                pass

            elif event_type == "message_delta":
                delta = data.get("delta", {})
                usage = normalize_usage(data.get("usage"))
                stop_reason = delta.get("stop_reason")

                if stop_reason and not turn_end_yielded:
                    turn_end_yielded = True
                    yield StreamEvent(kind="turn_end", usage=usage)

            elif event_type == "message_stop":
                # Final event — if message_delta didn't yield turn_end,
                # yield it here as safety net
                if not turn_end_yielded:
                    yield StreamEvent(kind="turn_end", usage=None)

            elif event_type == "error":
                err = data.get("error", {})
                yield StreamEvent(
                    kind="error",
                    text=err.get("message", str(data)),
                )
