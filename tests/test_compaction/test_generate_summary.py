"""Tests for generate_summary in code_agent/compaction/summarizer.py.

Covers the core LLM orchestration path:
- Normal call returns a cleaned summary string (prefix stripped, edges trimmed)
- _clean_summary preprocessing (no surrounding blank lines)
- Empty conversation -> ValueError
- LLM failure during chat -> wrapped in RuntimeError
- max_tokens=2048 passed to the client
- Retry loop: transient failure then success recovers
- Deterministic failures (auth/value errors) are not retried
- Two transient failures -> RuntimeError
"""

from __future__ import annotations

import httpx
import pytest

from code_agent.compaction.summarizer import generate_summary

from conftest import MockChatClient, make_text_response


class SummarizerChatClient(MockChatClient):
    """MockChatClient extended with the plain-text ``chat`` method the summarizer needs.

    generate_summary requires a client implementing the ``SummaryClient``
    protocol (``async chat(messages, max_tokens) -> str``), not the streaming
    ``chat_stream`` the shared MockChatClient provides. This subclass adds
    ``chat`` while reusing MockChatClient's ``call_history`` so tests can assert
    on the kwargs (e.g. ``max_tokens``) the summarizer passes through.
    """

    def __init__(self) -> None:
        super().__init__()
        self._chat_responses: list[str | BaseException] = []

    def set_chat_responses(self, responses: list[str | BaseException]) -> None:
        """Set the sequence of ``chat`` results (strings or exceptions to raise)."""
        self._chat_responses = list(responses)

    async def chat(self, messages: list[dict], max_tokens: int) -> str:
        """Record the call and return the next configured response."""
        self.call_history.append({"messages": messages, "max_tokens": max_tokens})
        if self._chat_responses:
            response = self._chat_responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        # Default text, derived from the shared stream-event helper
        return "".join(
            e.text
            for e in make_text_response("Default summary.")
            if e.kind == "text_delta"
        )


CONVERSATION = [
    {"role": "user", "content": "Help me implement a sorting function."},
    {"role": "assistant", "content": "Here is a naive bubble sort..."},
    {"role": "user", "content": "Make it in-place."},
]

SYSTEM_PROMPT = "You are a coding assistant."


class TestGenerateSummary:
    async def test_normal_call_returns_cleaned_summary(self):
        """A normal call returns a non-empty, cleaned summary string."""
        client = SummarizerChatClient()
        client.set_chat_responses(
            ["Here is the summary:\n\nThe fix is in code_agent/sort.py.\n"]
        )

        result = await generate_summary(client, CONVERSATION, SYSTEM_PROMPT)

        assert result
        assert "code_agent/sort.py" in result
        # LLM prefix artifact is stripped
        assert not result.lower().startswith("here is the summary")
        # Edges are trimmed
        assert result == result.strip()

    async def test_clean_strips_prefix_and_surrounding_blank_lines(self):
        """Summary is passed through _clean_summary: prefix + blank lines removed."""
        client = SummarizerChatClient()
        client.set_chat_responses(
            ["Here is a summary of the conversation:\n\n\nFixed the bug.\n\n\n\n"]
        )

        result = await generate_summary(client, CONVERSATION, SYSTEM_PROMPT)

        assert result == "Fixed the bug."
        # No excessive blank lines left at the edges
        assert "\n" not in result

    async def test_empty_conversation_raises_value_error(self):
        """Empty conversation raises ValueError before any LLM call."""
        client = SummarizerChatClient()

        with pytest.raises(ValueError, match="empty conversation"):
            await generate_summary(client, [], SYSTEM_PROMPT)

        # No LLM call was made
        assert client.call_history == []

    async def test_model_error_wrapped_in_runtime_error(self):
        """An unexpected LLM failure is wrapped in a RuntimeError."""
        client = SummarizerChatClient()
        client.set_chat_responses([RuntimeError("upstream LLM failure")])

        with pytest.raises(RuntimeError, match="Failed to generate compaction summary"):
            await generate_summary(client, CONVERSATION, SYSTEM_PROMPT)

    async def test_model_value_error_wrapped_without_retry(self):
        """A ValueError raised by the model is deterministic: wrapped, not retried."""
        client = SummarizerChatClient()
        client.set_chat_responses([ValueError("malformed response")])

        with pytest.raises(RuntimeError, match="Failed to generate compaction summary"):
            await generate_summary(client, CONVERSATION, SYSTEM_PROMPT)

        # Deterministic errors are not retried
        assert len(client.call_history) == 1

    async def test_auth_error_wrapped_without_retry(self):
        """An HTTP 401 auth failure is deterministic: wrapped, not retried."""
        request = httpx.Request("POST", "http://example.com/chat")
        error = httpx.HTTPStatusError(
            "Unauthorized",
            request=request,
            response=httpx.Response(401, request=request),
        )
        client = SummarizerChatClient()
        client.set_chat_responses([error])

        with pytest.raises(RuntimeError, match="Failed to generate compaction summary"):
            await generate_summary(client, CONVERSATION, SYSTEM_PROMPT)

        assert len(client.call_history) == 1

    async def test_max_tokens_2048_passed_to_client(self):
        """max_tokens=2048 is passed through to the client."""
        client = SummarizerChatClient()
        client.set_chat_responses(["Summary text."])

        await generate_summary(client, CONVERSATION, SYSTEM_PROMPT)

        assert len(client.call_history) == 1
        assert client.call_history[0]["max_tokens"] == 2048
        # Messages: summarization system prompt + user request
        messages = client.call_history[0]["messages"]
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "Please summarize the following conversation" in messages[1]["content"]

    async def test_retry_recovers_after_transient_http_500(self):
        """A transient HTTP 500 is retried and a later success is returned."""
        request = httpx.Request("POST", "http://example.com/chat")
        server_error = httpx.HTTPStatusError(
            "Internal Server Error",
            request=request,
            response=httpx.Response(500, request=request),
        )
        client = SummarizerChatClient()
        client.set_chat_responses([server_error, "Recovered summary."])

        result = await generate_summary(client, CONVERSATION, SYSTEM_PROMPT)

        assert result == "Recovered summary."
        assert len(client.call_history) == 2

    async def test_retry_recovers_after_connect_error(self):
        """A transient connect error is retried and a later success is returned."""
        client = SummarizerChatClient()
        client.set_chat_responses(
            [httpx.ConnectError("connection refused"), "Retried summary."]
        )

        result = await generate_summary(client, CONVERSATION, SYSTEM_PROMPT)

        assert result == "Retried summary."
        assert len(client.call_history) == 2

    async def test_two_transient_failures_raise_runtime_error(self):
        """Two consecutive transient failures raise a RuntimeError after retry."""
        request = httpx.Request("POST", "http://example.com/chat")
        server_error = httpx.HTTPStatusError(
            "Internal Server Error",
            request=request,
            response=httpx.Response(500, request=request),
        )
        client = SummarizerChatClient()
        client.set_chat_responses([server_error, server_error])

        with pytest.raises(RuntimeError, match="Failed to generate compaction summary"):
            await generate_summary(client, CONVERSATION, SYSTEM_PROMPT)

        assert len(client.call_history) == 2
