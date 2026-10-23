"""Backend protocol and request data structure.

Each LLM API backend implements the Backend Protocol with two methods:
- build_request: Session messages + tool defs -> wire format HTTP request
- parse_stream: SSE event JSON stream -> unified StreamEvent stream

Logic reference: Rust version trait separation — build_conversation_request()
constructs the request, stream_*() functions parse the response.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from code_agent.api.schemas import ProviderConfig, StreamEvent


@dataclass
class BackendRequest:
    """HTTP request constructed by a backend.

    Attributes:
        url: Full request URL (base_url + endpoint).
        headers: HTTP headers (includes auth header).
        body: Request body JSON dict.
    """

    url: str
    headers: dict[str, str]
    body: dict


def normalize_usage(usage: dict | None) -> dict | None:
    """Normalize a provider usage payload into unified fields.

    Returns a dict (or None) with keys:
        prompt_tokens, completion_tokens, cached_prompt_tokens,
        reasoning_tokens

    Handles both wire shapes:
    - OpenAI Chat Completions / Responses: ``prompt_tokens`` /
      ``completion_tokens`` (+ ``prompt_tokens_details.cached_tokens``,
      ``completion_tokens_details.reasoning_tokens``).
    - Anthropic Messages: ``input_tokens`` / ``output_tokens`` (+
      ``cache_read_input_tokens`` / ``cache_creation_input_tokens``).

    The Agent consumes this unified shape when recording real usage into
    the Session ledger (see ``Session.record_usage``).
    """
    if not usage:
        return None

    if "prompt_tokens" in usage or "completion_tokens" in usage:
        details = usage.get("prompt_tokens_details", {}) or {}
        cdetails = usage.get("completion_tokens_details", {}) or {}
        return {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "cached_prompt_tokens": int(details.get("cached_tokens") or 0),
            "reasoning_tokens": int(cdetails.get("reasoning_tokens") or 0),
        }

    # Anthropic Messages style
    cached = int(usage.get("cache_read_input_tokens") or 0)
    created = int(usage.get("cache_creation_input_tokens") or 0)
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    return {
        "prompt_tokens": inp + cached + created,
        "completion_tokens": out,
        "cached_prompt_tokens": cached,
        "reasoning_tokens": 0,
    }


class Backend(Protocol):
    """LLM API backend protocol.

    Each backend (ChatCompletions, Messages, Responses) implements this
    protocol. Backends are stateless — instances can be singletons.

    Separation of concerns:
    - build_request: converts Session.to_api_messages() output to the
      backend's wire format (message structure, tool format, auth headers)
    - parse_stream: converts the backend's SSE JSON chunks to unified
      StreamEvent instances (text_delta, tool_call_delta, thinking_delta,
      turn_end, error)
    """

    def build_request(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        config: "ProviderConfig",
    ) -> BackendRequest:
        """Construct the HTTP request (url + headers + body)."""
        ...

    async def parse_stream(
        self,
        sse_events: AsyncIterator[dict],
    ) -> AsyncIterator["StreamEvent"]:
        """Parse SSE event stream, yield unified StreamEvent instances."""
        ...
