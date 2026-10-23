"""OpenAI Responses API backend.

Wire format: POST {base_url}/responses
Applicable to: OpenAI Responses API (new generation API).

Key differences from Chat Completions:
- Uses "input" array (not "messages") with typed items
- Message items: {type: "message", role, content: [{type: "input_text"/"output_text", text}]}
- Tool calls: {type: "function_call", call_id, name, arguments}
- Tool results: {type: "function_call_output", call_id, output}
- Streaming uses event-typed SSE with response.* prefix
- Tool calls keyed by output_index (server-assigned), not positional index
- store: false for ZDR compliance

Message conversion: Session.to_api_messages() outputs OpenAI Chat Completions
format. This backend converts to Responses API input format in build_request().

Logic reference: Rust version stream_responses() (stream/responses.rs)
+ impl From<&ConversationRequest> for rs::CreateResponse (conversation.rs:2104).
"""

from __future__ import annotations

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

_ENDPOINT = "responses"


class ResponsesBackend:
    """OpenAI Responses API backend.

    Stateless — safe to use as singleton.
    """

    def build_request(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        config: ProviderConfig,
    ) -> BackendRequest:
        """Construct HTTP request for /responses endpoint.

        Converts OpenAI Chat Completions messages to Responses API
        input format: typed items (message/function_call/function_call_output).
        """
        url = f"{config.base_url.rstrip('/')}/{_ENDPOINT}"

        # Responses API uses Bearer auth
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        }

        input_items = self._convert_messages(messages)

        body: dict = {
            "model": config.model,
            "input": input_items,
            "max_output_tokens": config.max_tokens,
            "temperature": config.temperature,
            "stream": True,
            "store": False,  # ZDR compliance
        }
        if tools:
            converted: list[dict] = []
            for tool in tools:
                func = tool.get("function", {})
                converted.append({
                    "type": "function",
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "parameters": func.get("parameters", {"type": "object"}),
                })
            body["tools"] = converted
            body["tool_choice"] = "auto"

        return BackendRequest(url=url, headers=headers, body=body)

    @staticmethod
    def _convert_messages(api_messages: list[dict]) -> list[dict]:
        """Convert OpenAI Chat Completions messages to Responses input format.

        Conversion rules:
        1. role=system -> {type: "message", role: "system",
           content: [{type: "input_text", text}]}
        2. role=user -> {type: "message", role: "user",
           content: [{type: "input_text", text}]}
        3. role=assistant:
           - content -> {type: "message", role: "assistant",
             content: [{type: "output_text", text}]}
           - tool_calls -> each {type: "function_call", call_id, name, arguments}
        4. role=tool -> {type: "function_call_output", call_id, output}
        """
        input_items: list[dict] = []

        for msg in api_messages:
            role = msg["role"]

            if role in ("system", "user"):
                content_type = "input_text"
                input_items.append({
                    "type": "message",
                    "role": role,
                    "content": [{
                        "type": content_type,
                        "text": msg.get("content", ""),
                    }],
                })

            elif role == "assistant":
                if msg.get("content"):
                    input_items.append({
                        "type": "message",
                        "role": "assistant",
                        "content": [{
                            "type": "output_text",
                            "text": msg["content"],
                        }],
                    })
                for tc in msg.get("tool_calls", []):
                    func = tc.get("function", {})
                    input_items.append({
                        "type": "function_call",
                        "call_id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "arguments": func.get("arguments", "{}"),
                    })

            elif role == "tool":
                input_items.append({
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id", ""),
                    "output": msg.get("content", ""),
                })

        return input_items

    async def parse_stream(
        self,
        sse_events: AsyncIterator[dict],
    ) -> AsyncIterator[StreamEvent]:
        """Parse Responses API SSE events into StreamEvent.

        Responses API uses event-typed SSE with response.* prefix.
        Tool calls are keyed by output_index (server-assigned), not
        a client-controlled positional index. We maintain an
        output_to_tool map, assigning sequential tool indices.
        """
        output_to_tool: dict[int, int] = {}
        next_tool_index = 0

        async for event in sse_events:
            event_type = event.get("_event", "")
            data = {k: v for k, v in event.items() if k != "_event"}

            if event_type == "response.output_text.delta":
                yield StreamEvent(kind="text_delta", text=data.get("delta", ""))

            elif event_type == "response.reasoning_summary_text.delta":
                yield StreamEvent(kind="thinking_delta", text=data.get("delta", ""))

            elif event_type == "response.reasoning_text.delta":
                yield StreamEvent(kind="thinking_delta", text=data.get("delta", ""))

            elif event_type == "response.output_item.added":
                item = data.get("item", {})
                if item.get("type") == "function_call":
                    out_idx = data.get("output_index", 0)
                    tool_idx = next_tool_index
                    next_tool_index += 1
                    output_to_tool[out_idx] = tool_idx
                    yield StreamEvent(
                        kind="tool_call_delta",
                        tool_call=ToolCallDelta(
                            index=tool_idx,
                            id=item.get("call_id"),
                            name=item.get("name"),
                        ),
                    )

            elif event_type == "response.function_call_arguments.delta":
                out_idx = data.get("output_index", 0)
                tool_idx = output_to_tool.get(out_idx)
                if tool_idx is not None:
                    yield StreamEvent(
                        kind="tool_call_delta",
                        tool_call=ToolCallDelta(
                            index=tool_idx,
                            arguments_fragment=data.get("delta", ""),
                        ),
                    )

            elif event_type in ("response.completed", "response.incomplete"):
                response_data = data.get("response", {})
                usage = normalize_usage(response_data.get("usage"))
                yield StreamEvent(kind="turn_end", usage=usage)

            elif event_type in ("response.failed", "response.error"):
                error = data.get("error", {})
                yield StreamEvent(
                    kind="error",
                    text=error.get("message", str(data)),
                )
