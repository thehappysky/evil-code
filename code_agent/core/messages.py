"""Message data models for Agent internal communication.

Logic reference: Rust version ConversationItem enum
(sampling-types/src/conversation.rs:28-58).

Tagged union design: each message type has a distinct 'role' field.
Pydantic BaseModel + Literal role achieves tagged union in Python.

Key design decisions:
- arguments stored as raw JSON string — parsing deferred to ToolRegistry.execute()
- is_error on ToolResultMessage lets model decide to retry or abandon
- Does NOT include Rust version's BackendToolCall or Reasoning variants (Phase 1)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ToolCallItem(BaseModel):
    """A single tool call within an AssistantMessage.

    Attributes:
        id: Unique call ID (assigned by the model, used to correlate results).
        name: Tool name to invoke.
        arguments: Raw JSON string of arguments.
            Parsing is deferred to ToolRegistry.execute() so that
            the message layer does not depend on the tool layer.
    """

    id: str
    name: str
    arguments: str = ""


class SystemMessage(BaseModel):
    """System instruction message (conversation[0]).

    Attributes:
        role: Always "system".
        content: System prompt text.
    """

    role: Literal["system"] = "system"
    content: str


class UserMessage(BaseModel):
    """User input message (may contain synthetic system-reminder blocks).

    Attributes:
        role: Always "user".
        content: User's input text.
    """

    role: Literal["user"] = "user"
    content: str


class AssistantMessage(BaseModel):
    """Model response message.

    May contain text content, tool calls, or both.
    A pure text response has tool_calls=None.
    A tool-calling response may have content=None (or brief text) + tool_calls list.

    Attributes:
        role: Always "assistant".
        content: Text response (may be None if only tool calls).
        tool_calls: List of tool calls requested by the model.
    """

    role: Literal["assistant"] = "assistant"
    content: str | None = None
    tool_calls: list[ToolCallItem] | None = None


class ToolResultMessage(BaseModel):
    """Tool execution result fed back to the model.

    Attributes:
        role: Always "tool".
        tool_call_id: Correlates with the ToolCallItem.id that triggered this.
        content: Result text (may be truncated).
        is_error: Whether execution failed. Model sees this and may retry.
    """

    role: Literal["tool"] = "tool"
    tool_call_id: str
    content: str
    is_error: bool = False


# Type alias for the tagged union
Message = SystemMessage | UserMessage | AssistantMessage | ToolResultMessage
