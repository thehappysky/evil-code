"""Tests for api/client.py: refactored ChatClient (httpx + backend dispatch).

Covers:
- Missing api_key -> AuthenticationError
- HTTP 401 -> AuthenticationError
- HTTP non-200 -> error StreamEvent
- Successful stream -> StreamEvent sequence via backend
- Per-request max_tokens/temperature override
- close() cleanup
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from code_agent.api.client import ChatClient
from code_agent.api.schemas import ApiBackend, AuthScheme, ProviderConfig, StreamEvent
from code_agent.core.exceptions import AuthenticationError


def _make_config(
    api_key: str = "sk-test",
    api_backend: ApiBackend = ApiBackend.CHAT_COMPLETIONS,
    base_url: str = "https://api.test.com/v1",
) -> ProviderConfig:
    return ProviderConfig(
        api_backend=api_backend,
        base_url=base_url,
        api_key=api_key,
        auth_scheme=AuthScheme.BEARER,
        model="test-model",
    )


class TestChatClientAuth:
    async def test_missing_api_key_raises(self):
        """ChatClient raises AuthenticationError when api_key is None."""
        config = ProviderConfig(api_key=None)
        client = ChatClient(config)

        with pytest.raises(AuthenticationError, match="No API key"):
            async for _ in client.chat_stream([]):
                pass

    async def test_401_raises_auth_error(self, httpx_mock):
        """HTTP 401 response raises AuthenticationError."""
        httpx_mock.add_response(
            method="POST",
            url="https://api.test.com/v1/chat/completions",
            status_code=401,
            content=b'{"error": "invalid api key"}',
        )

        client = ChatClient(_make_config())
        with pytest.raises(AuthenticationError, match="401"):
            async for _ in client.chat_stream([]):
                pass

    async def test_non_200_yields_error_event(self, httpx_mock):
        """HTTP 500 response yields an error StreamEvent."""
        httpx_mock.add_response(
            method="POST",
            url="https://api.test.com/v1/chat/completions",
            status_code=500,
            content=b'{"error": "server error"}',
        )

        client = ChatClient(_make_config())
        events = [e async for e in client.chat_stream([])]

        assert len(events) == 1
        assert events[0].kind == "error"
        assert "500" in events[0].text


class TestChatClientStream:
    async def test_successful_stream(self, httpx_mock):
        """Successful stream yields text_delta + turn_end events."""
        # SSE response body for Chat Completions format
        sse_lines = [
            'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}',
 "",
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5}}',
 "",
            "data: [DONE]",
 "",
        ]
        sse_body = "\n".join(sse_lines)

        httpx_mock.add_response(
            method="POST",
            url="https://api.test.com/v1/chat/completions",
            status_code=200,
            content=sse_body.encode("utf-8"),
            headers={"content-type": "text/event-stream"},
        )

        client = ChatClient(_make_config())
        events = [e async for e in client.chat_stream([{"role": "user", "content": "hi"}])]

        # Should get text_delta + turn_end
        kinds = [e.kind for e in events]
        assert "text_delta" in kinds
        assert "turn_end" in kinds
        text_events = [e for e in events if e.kind == "text_delta"]
        assert text_events[0].text == "Hello"

    async def test_per_request_override(self, httpx_mock):
        """max_tokens and temperature overrides are applied."""
        captured_body = {}

        def _capture(request):
            captured_body.update(json.loads(request.content))
            return httpx.Response(
                status_code=200,
                content=b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
                headers={"content-type": "text/event-stream"},
            )

        httpx_mock.add_callback(_capture)

        client = ChatClient(_make_config())
        async for _ in client.chat_stream([], max_tokens=100, temperature=0.1):
            pass

        assert captured_body["max_tokens"] == 100
        assert captured_body["temperature"] == 0.1


class TestChatClientClose:
    async def test_close_cleanup(self):
        """close() closes the httpx client."""
        client = ChatClient(_make_config())
        assert client._http_client is not None
        await client.close()
        assert client._http_client is None
