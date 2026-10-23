"""API data models for LLM communication.

Logic reference: Rust version ConversationItem tagged enum
(sampling-types/src/conversation.rs:28).

Pydantic + Literal types achieve tagged union effect in Python.
ToolCall.arguments stores raw JSON string — parsing deferred to ToolRegistry.
StreamEvent.kind uses Literal to distinguish event types.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ─── Tool Definition (sent TO the model) ───────────────────────────────────────


class FunctionSchema(BaseModel):
    """Function description within a tool definition."""

    name: str
    description: str
    parameters: dict = Field(default_factory=dict)


class ToolDefinitionSchema(BaseModel):
    """OpenAI function-calling format tool definition (sent to model)."""

    type: Literal["function"] = "function"
    function: FunctionSchema


# ─── Tool Call (received FROM the model) ───────────────────────────────────────


class FunctionCall(BaseModel):
    """Function call details from model response.

    Attributes:
        name: Tool name the model wants to call.
        arguments: Raw JSON string (not parsed here — ToolRegistry handles it).
    """

    name: str
    arguments: str = ""


class ToolCall(BaseModel):
    """A tool call request from the model.

    Attributes:
        id: Unique call ID for correlating with tool results.
        type: Always "function".
        function: The function name and arguments.
    """

    id: str
    type: Literal["function"] = "function"
    function: FunctionCall


# ─── Stream Events (yielded by ChatClient) ─────────────────────────────────────


class ToolCallDelta(BaseModel):
    """Incremental tool call data during streaming.

    In SSE streaming, tool calls arrive in fragments:
    - First chunk: index + id + name
    - Subsequent chunks: index + arguments_fragment

    The Agent accumulates these by index into complete ToolCallItems.
    """

    index: int
    id: str | None = None
    name: str | None = None
    arguments_fragment: str | None = None


class StreamEvent(BaseModel):
    """A single event in the streaming response.

    kind discriminates the event type:
    - text_delta: Incremental text content from the model
    - tool_call_delta: Incremental tool call data (accumulate by index)
    - thinking_delta: Reasoning/thinking tokens (Phase 1: defined, Phase 3: rendered)
    - turn_end: Model finished this turn (check pending_calls for tool calls)
    - error: An error occurred during streaming

    Attributes:
        kind: Event type discriminator.
        text: Text content (for text_delta and thinking_delta).
        tool_call: Tool call fragment (for tool_call_delta).
        usage: Token usage info (present on turn_end if available).
    """

    kind: Literal[
        "text_delta", "tool_call_delta", "thinking_delta", "turn_end", "error"
    ]
    text: str | None = None
    tool_call: ToolCallDelta | None = None
    usage: dict | None = None


# ─── Multi-Provider Configuration (Phase 1 Enhancement) ─────────────────────


class ApiBackend(Enum):
    """LLM API backend protocol. Determines request body format and SSE parsing.

    Each model/provider declares which backend it uses:
    - CHAT_COMPLETIONS: /v1/chat/completions (OpenAI/xAI/Ollama/compatible endpoints)
    - MESSAGES: /v1/messages (Anthropic native + compatible endpoints like DeepSeek)
    - RESPONSES: /v1/responses (OpenAI Responses API)
    """

    CHAT_COMPLETIONS = "chat_completions"
    MESSAGES = "messages"
    RESPONSES = "responses"


class AuthScheme(Enum):
    """HTTP authentication header scheme.

    Bearer: Authorization: Bearer {key} (OpenAI/xAI/Claude OAuth)
    XApiKey: x-api-key: {key} (Anthropic native API key)
    """

    BEARER = "bearer"
    X_API_KEY = "x_api_key"


@dataclass
class ProviderConfig:
    """Complete configuration for a single LLM provider.

    Held by ChatClient. Constructed by cli/main.py from the 4-level
    auth resolution chain (CLI args > env vars > settings.json > OAuth).

    Attributes:
        api_backend: Backend protocol (determines wire format).
        base_url: API base URL (e.g. https://api.anthropic.com/v1).
        api_key: API key (direct key or OAuth accessToken).
        auth_scheme: Authentication header scheme.
        model: Model ID.
        max_tokens: Maximum output tokens.
        temperature: Sampling temperature.
    """

    api_backend: ApiBackend = ApiBackend.CHAT_COMPLETIONS
    base_url: str = "https://api.deepseek.com/anthropic"
    api_key: str | None = None
    auth_scheme: AuthScheme = AuthScheme.BEARER
    model: str = "deepseek-v4-pro"
    max_tokens: int = 4096
    temperature: float = 0.7
