"""Tests for api/backends/: three backend implementations.

Covers build_request (URL, headers, body structure) and
parse_stream (StreamEvent output from SSE input) for each backend.

ChatCompletions: /chat/completions, OpenAI format
Messages: /messages, Anthropic format (message conversion + tool conversion)
Responses: /responses, Responses API format (input items)
"""

from __future__ import annotations

import json

import pytest

from code_agent.api.backends.chat_completions import ChatCompletionsBackend
from code_agent.api.backends.messages import MessagesBackend
from code_agent.api.backends.responses import ResponsesBackend
from code_agent.api.schemas import (
    ApiBackend,
    AuthScheme,
    ProviderConfig,
    StreamEvent,
)


def _config(
    backend: ApiBackend = ApiBackend.CHAT_COMPLETIONS,
    auth_scheme: AuthScheme = AuthScheme.BEARER,
    base_url: str = "https://api.test.com/v1",
) -> ProviderConfig:
    return ProviderConfig(
        api_backend=backend,
        base_url=base_url,
        api_key="sk-test-123",
        auth_scheme=auth_scheme,
        model="test-model",
    )


# ─── Chat Completions Backend ─────────────────────────────────────────────────


class TestChatCompletionsBuild:
    def test_url_and_headers(self):
        backend = ChatCompletionsBackend()
        req = backend.build_request([], None, _config())
        assert req.url == "https://api.test.com/v1/chat/completions"
        assert req.headers["Authorization"] == "Bearer sk-test-123"
        assert req.headers["Content-Type"] == "application/json"

    def test_body_structure(self):
        backend = ChatCompletionsBackend()
        req = backend.build_request(
            [{"role": "user", "content": "hi"}], None, _config()
        )
        assert req.body["model"] == "test-model"
        assert req.body["stream"] is True
        assert req.body["stream_options"] == {"include_usage": True}
        assert "tools" not in req.body

    def test_tools_included(self):
        backend = ChatCompletionsBackend()
        tools = [{"type": "function", "function": {"name": "echo", "description": "d", "parameters": {}}}]
        req = backend.build_request([], tools, _config())
        assert req.body["tools"] == tools
        assert req.body["tool_choice"] == "auto"

    def test_xapikey_header(self):
        backend = ChatCompletionsBackend()
        req = backend.build_request([], None, _config(auth_scheme=AuthScheme.X_API_KEY))
        assert req.headers["x-api-key"] == "sk-test-123"
        assert "Authorization" not in req.headers


class TestChatCompletionsParse:
    async def test_text_stream(self):
        """Parse text content + finish_reason into StreamEvents."""
        async def sse_gen():
            yield {
                "choices": [{
                    "delta": {"content": "Hello"},
                    "finish_reason": None,
                }]
            }
            yield {
                "choices": [{
                    "delta": {},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }

        backend = ChatCompletionsBackend()
        events = [e async for e in backend.parse_stream(sse_gen())]
        assert events[0].kind == "text_delta"
        assert events[0].text == "Hello"
        assert events[-1].kind == "turn_end"
        assert events[-1].usage["prompt_tokens"] == 10

    async def test_tool_call_stream(self):
        """Parse tool_call deltas by index."""
        async def sse_gen():
            yield {
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "id": "call_1",
                            "function": {"name": "echo", "arguments": ""},
                        }]
                    },
                    "finish_reason": None,
                }]
            }
            yield {
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "function": {"arguments": '{"text":"hi"}'},
                        }]
                    },
                    "finish_reason": "tool_calls",
                }]
            }

        backend = ChatCompletionsBackend()
        events = [e async for e in backend.parse_stream(sse_gen())]
        tc_events = [e for e in events if e.kind == "tool_call_delta"]
        assert len(tc_events) == 2
        assert tc_events[0].tool_call.id == "call_1"
        assert tc_events[0].tool_call.name == "echo"
        assert tc_events[1].tool_call.arguments_fragment == '{"text":"hi"}'

    async def test_done_terminator(self):
        """data: [DONE] terminates the stream."""
        async def sse_gen():
            yield {"_done": True}

        backend = ChatCompletionsBackend()
        events = [e async for e in backend.parse_stream(sse_gen())]
        assert len(events) == 0


# ─── Messages Backend ──────────────────────────────────────────────────────────


class TestMessagesBuild:
    def test_url_and_headers(self):
        backend = MessagesBackend()
        req = backend.build_request([], None, _config(ApiBackend.MESSAGES))
        assert req.url == "https://api.test.com/v1/messages"
        assert req.headers["anthropic-version"] == "2023-06-01"

    def test_bearer_auth(self):
        backend = MessagesBackend()
        req = backend.build_request([], None, _config(ApiBackend.MESSAGES, AuthScheme.BEARER))
        assert req.headers["Authorization"] == "Bearer sk-test-123"

    def test_xapikey_auth(self):
        backend = MessagesBackend()
        req = backend.build_request([], None, _config(ApiBackend.MESSAGES, AuthScheme.X_API_KEY))
        assert req.headers["x-api-key"] == "sk-test-123"
        assert "Authorization" not in req.headers

    def test_system_extraction(self):
        """system role messages are extracted to top-level system field."""
        backend = MessagesBackend()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hi"},
        ]
        req = backend.build_request(messages, None, _config(ApiBackend.MESSAGES))
        assert req.body["system"] == "You are helpful."
        assert req.body["messages"] == [{"role": "user", "content": "hi"}]

    def test_tool_result_conversion(self):
        """role=tool messages become tool_result blocks in user messages."""
        backend = MessagesBackend()
        messages = [
            {"role": "user", "content": "list files"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "function": {"name": "shell_exec", "arguments": '{"command":"ls"}'}}
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "file1.txt"},
        ]
        req = backend.build_request(messages, None, _config(ApiBackend.MESSAGES))
        # Last message should be role=user with tool_result content
        last_msg = req.body["messages"][-1]
        assert last_msg["role"] == "user"
        block = last_msg["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "call_1"

    def test_tool_definition_conversion(self):
        """OpenAI tool defs → Anthropic format (input_schema)."""
        backend = MessagesBackend()
        tools = [{"type": "function", "function": {
            "name": "echo", "description": "echo", "parameters": {"type": "object"}
        }}]
        req = backend.build_request([], tools, _config(ApiBackend.MESSAGES))
        assert req.body["tools"][0]["name"] == "echo"
        assert req.body["tools"][0]["input_schema"] == {"type": "object"}


class TestMessagesParse:
    async def test_text_delta(self):
        """content_block_delta text_delta → text_delta StreamEvent."""
        async def sse_gen():
            yield {"_event": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
            yield {"_event": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}}
            yield {"_event": "content_block_stop", "index": 0}
            yield {"_event": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"input_tokens": 10, "output_tokens": 5}}
            yield {"_event": "message_stop"}

        backend = MessagesBackend()
        events = [e async for e in backend.parse_stream(sse_gen())]
        text_events = [e for e in events if e.kind == "text_delta"]
        assert len(text_events) == 1
        assert text_events[0].text == "Hello"
        end_events = [e for e in events if e.kind == "turn_end"]
        assert len(end_events) == 1

    async def test_tool_use_stream(self):
        """Tool use: content_block_start(tool_use) + input_json_delta."""
        async def sse_gen():
            yield {"_event": "content_block_start", "index": 0,
                   "content_block": {"type": "tool_use", "id": "call_1", "name": "echo"}}
            yield {"_event": "content_block_delta", "index": 0,
                   "delta": {"type": "input_json_delta", "partial_json": '{"text":"hi"}'}}
            yield {"_event": "content_block_stop", "index": 0}
            yield {"_event": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {}}
            yield {"_event": "message_stop"}

        backend = MessagesBackend()
        events = [e async for e in backend.parse_stream(sse_gen())]
        tc_events = [e for e in events if e.kind == "tool_call_delta"]
        # First: id+name, Second: arguments fragment
        assert len(tc_events) == 2
        assert tc_events[0].tool_call.id == "call_1"
        assert tc_events[0].tool_call.name == "echo"
        assert tc_events[1].tool_call.arguments_fragment == '{"text":"hi"}'


# ─── Responses Backend ─────────────────────────────────────────────────────────


class TestResponsesBuild:
    def test_url_and_headers(self):
        backend = ResponsesBackend()
        req = backend.build_request([], None, _config(ApiBackend.RESPONSES))
        assert req.url == "https://api.test.com/v1/responses"
        assert req.headers["Authorization"] == "Bearer sk-test-123"

    def test_input_format(self):
        """Messages converted to Responses input format."""
        backend = ResponsesBackend()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hi"},
        ]
        req = backend.build_request(messages, None, _config(ApiBackend.RESPONSES))
        items = req.body["input"]
        assert items[0]["type"] == "message"
        assert items[0]["role"] == "system"
        assert items[1]["content"][0]["type"] == "input_text"

    def test_store_false(self):
        """store: false for ZDR compliance."""
        backend = ResponsesBackend()
        req = backend.build_request([], None, _config(ApiBackend.RESPONSES))
        assert req.body["store"] is False

    def test_max_output_tokens(self):
        """Responses API uses max_output_tokens (not max_tokens)."""
        backend = ResponsesBackend()
        req = backend.build_request([], None, _config(ApiBackend.RESPONSES))
        assert "max_output_tokens" in req.body
        assert "max_tokens" not in req.body

    def test_tool_call_conversion(self):
        """Assistant tool_calls → function_call items."""
        backend = ResponsesBackend()
        messages = [
            {"role": "user", "content": "run ls"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "function": {"name": "shell_exec", "arguments": '{"command":"ls"}'}}
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "output"},
        ]
        req = backend.build_request(messages, None, _config(ApiBackend.RESPONSES))
        items = req.body["input"]
        # function_call item
        fc = [i for i in items if i.get("type") == "function_call"]
        assert len(fc) == 1
        assert fc[0]["call_id"] == "call_1"
        # function_call_output item
        fco = [i for i in items if i.get("type") == "function_call_output"]
        assert len(fco) == 1
        assert fco[0]["output"] == "output"


class TestResponsesParse:
    async def test_text_delta(self):
        """response.output_text.delta → text_delta StreamEvent."""
        async def sse_gen():
            yield {"_event": "response.output_text.delta", "delta": "Hello"}
            yield {"_event": "response.completed", "response": {"usage": {"input_tokens": 10, "output_tokens": 5}}}

        backend = ResponsesBackend()
        events = [e async for e in backend.parse_stream(sse_gen())]
        assert events[0].kind == "text_delta"
        assert events[0].text == "Hello"
        assert events[1].kind == "turn_end"

    async def test_tool_call_stream(self):
        """Function call: output_item.added + arguments.delta."""
        async def sse_gen():
            yield {"_event": "response.output_item.added", "output_index": 0,
                   "item": {"type": "function_call", "call_id": "call_1", "name": "echo"}}
            yield {"_event": "response.function_call_arguments.delta", "output_index": 0,
                   "delta": '{"text":"hi"}'}
            yield {"_event": "response.completed", "response": {"usage": {}}}

        backend = ResponsesBackend()
        events = [e async for e in backend.parse_stream(sse_gen())]
        tc_events = [e for e in events if e.kind == "tool_call_delta"]
        assert len(tc_events) == 2
        assert tc_events[0].tool_call.id == "call_1"
        assert tc_events[0].tool_call.name == "echo"
        assert tc_events[1].tool_call.arguments_fragment == '{"text":"hi"}'


# [Phase 5] 2026-08-04: normalize_usage unification
class TestNormalizeUsage:
    def test_none_and_empty(self):
        from code_agent.api.backends.base import normalize_usage
        assert normalize_usage(None) is None
        assert normalize_usage({}) is None

    def test_openai_chat_style(self):
        from code_agent.api.backends.base import normalize_usage
        out = normalize_usage({
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "prompt_tokens_details": {"cached_tokens": 30},
            "completion_tokens_details": {"reasoning_tokens": 8},
        })
        assert out == {
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "cached_prompt_tokens": 30,
            "reasoning_tokens": 8,
        }

    def test_openai_without_details(self):
        from code_agent.api.backends.base import normalize_usage
        out = normalize_usage({"prompt_tokens": 5, "completion_tokens": 3})
        assert out["cached_prompt_tokens"] == 0
        assert out["reasoning_tokens"] == 0

    def test_anthropic_messages_style(self):
        from code_agent.api.backends.base import normalize_usage
        out = normalize_usage({
            "input_tokens": 80,
            "output_tokens": 20,
            "cache_read_input_tokens": 40,
            "cache_creation_input_tokens": 10,
        })
        # prompt = input + cache_read + cache_creation = 130
        assert out["prompt_tokens"] == 130
        assert out["completion_tokens"] == 20
        assert out["cached_prompt_tokens"] == 40
