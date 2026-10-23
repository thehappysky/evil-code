"""OpenAI Chat Completions backend.

Wire format: POST {base_url}/chat/completions
Applicable to: OpenAI, xAI, Ollama, DeepSeek (OpenAI-compatible endpoint),
and any OpenAI-compatible API.

Messages from Session.to_api_messages() are already in OpenAI format
(role/content/tool_calls/tool_call_id) — no conversion needed.

SSE format: "data: {json}\\n\\n" lines, terminated by "data: [DONE]".
Each chunk has choices[0].delta with content/tool_calls/reasoning_content.
Tool calls stream by positional index: first chunk has id+name,
subsequent chunks only have function.arguments fragments.

Logic reference: Rust version stream_chat_completions()
(stream/chat_completions.rs).
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

_ENDPOINT = "chat/completions"


class ChatCompletionsBackend:
    """OpenAI Chat Completions API backend.

    Stateless — safe to use as singleton.
    """

    def build_request(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        config: ProviderConfig,
    ) -> BackendRequest:
        """Construct HTTP request for /chat/completions endpoint.

        Messages are passed through as-is (already OpenAI format).
        Auth header determined by config.auth_scheme.
        """
        url = f"{config.base_url.rstrip('/')}/{_ENDPOINT}"

        # Auth header: Bearer or XApiKey
        if config.auth_scheme == AuthScheme.X_API_KEY:
            headers = {
                "Content-Type": "application/json",
                "x-api-key": config.api_key or "",
            }
        else:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.api_key}",
            }

        body: dict = {
            "model": config.model,
            "messages": messages,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        return BackendRequest(url=url, headers=headers, body=body)

    async def parse_stream(
        self,
        sse_events: AsyncIterator[dict],
    ) -> AsyncIterator[StreamEvent]:
        """Parse Chat Completions SSE chunks into StreamEvent.

        Tool call deltas are forwarded directly — the Agent accumulates
        them by index (agent.py pending_calls dict).
        """
        async for event in sse_events:
            if event.get("_done"):
                break  # [DONE] terminator

            choices = event.get("choices", [])
            if not choices:
                # Usage-only final chunk (stream_options.include_usage)
                usage = normalize_usage(event.get("usage"))
                if usage:
                    yield StreamEvent(kind="turn_end", usage=usage)
                continue

            choice = choices[0]
            delta = choice.get("delta", {})

            # Text content
            if delta.get("content"):
                yield StreamEvent(kind="text_delta", text=delta["content"])

            # Reasoning/thinking tokens (model-specific, e.g. xAI Grok)
            reasoning = delta.get("reasoning_content")
            if reasoning:
                yield StreamEvent(kind="thinking_delta", text=reasoning)

            # Tool call deltas (by positional index)
            tool_calls = delta.get("tool_calls", [])
            for tc in tool_calls:
                func = tc.get("function", {})
                yield StreamEvent(
                    kind="tool_call_delta",
                    tool_call=ToolCallDelta(
                        index=tc.get("index", 0),
                        id=tc.get("id"),
                        name=func.get("name"),
                        arguments_fragment=func.get("arguments"),
                    ),
                )

            # Finish reason
            finish = choice.get("finish_reason")
            if finish in ("stop", "tool_calls", "end_turn", "length"):
                usage_dict = normalize_usage(event.get("usage"))
                yield StreamEvent(kind="turn_end", usage=usage_dict)
