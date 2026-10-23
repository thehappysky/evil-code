# [Phase 5 / Phase 4] 2026-08-04: Tests for builtin/web_search.py
"""Tests for the web_search tool (Responses API, mocked httpx)."""

from __future__ import annotations

import json

import httpx
import pytest

from code_agent.api.schemas import ApiBackend, AuthScheme, ProviderConfig
from code_agent.tools.builtin.web_search import WebSearchInput, WebSearchTool
from code_agent.tools.result import ToolResult


def _config() -> ProviderConfig:
    return ProviderConfig(
        api_backend=ApiBackend.RESPONSES,
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        auth_scheme=AuthScheme.BEARER,
        model="test-model",
    )


def _response_payload(content: str = "The answer is 42.", citations=("https://a.com",)):
    return {
        "output": [
            {
                "content": [
                    {
                        "type": "output_text",
                        "text": content,
                        "annotations": [
                            {"type": "url_citation", "url": url} for url in citations
                        ],
                    }
                ]
            }
        ]
    }


class TestWebSearch:
    async def test_success_returns_answer_and_sources(self, httpx_mock):
        httpx_mock.add_response(
            status_code=200,
            content=json.dumps(_response_payload()).encode("utf-8"),
        )
        tool = WebSearchTool(_config())
        result = await tool.run(WebSearchInput(query="meaning of life"))

        assert isinstance(result, ToolResult)
        assert not result.is_error
        assert "The answer is 42." in result.output
        assert "https://a.com" in result.output
        # The request targeted the Responses API with the web_search tool.
        req = httpx_mock.get_requests()[0]
        assert req.url == "https://api.openai.com/v1/responses"
        body = json.loads(req.content)
        assert body["tools"][0]["type"] == "web_search"

    async def test_allowed_domains_passed(self, httpx_mock):
        httpx_mock.add_response(
            status_code=200, content=json.dumps(_response_payload()).encode("utf-8"),
        )
        tool = WebSearchTool(_config())
        await tool.run(WebSearchInput(query="q", allowed_domains=["github.com"]))
        body = json.loads(httpx_mock.get_requests()[0].content)
        assert body["tools"][0]["web_search"]["filters"]["allowed_domains"] == ["github.com"]

    async def test_citations_deduped(self, httpx_mock):
        payload = _response_payload(citations=("https://a.com", "https://a.com", "https://b.com"))
        httpx_mock.add_response(status_code=200, content=json.dumps(payload).encode("utf-8"))
        tool = WebSearchTool(_config())
        result = await tool.run(WebSearchInput(query="q"))
        assert result.output.count("https://a.com") == 1
        assert result.output.count("https://b.com") == 1

    async def test_no_results(self, httpx_mock):
        httpx_mock.add_response(
            status_code=200, content=json.dumps({"output": []}).encode("utf-8"),
        )
        tool = WebSearchTool(_config())
        result = await tool.run(WebSearchInput(query="nothing"))
        assert not result.is_error
        assert "No search results found." in result.output

    async def test_http_error(self, httpx_mock):
        httpx_mock.add_response(status_code=500, content=b'{"error":"boom"}')
        tool = WebSearchTool(_config())
        result = await tool.run(WebSearchInput(query="q"))
        assert result.is_error
        assert "500" in result.output

    async def test_connection_error(self, httpx_mock):
        httpx_mock.add_exception(httpx.ConnectError("refused"))
        tool = WebSearchTool(_config())
        result = await tool.run(WebSearchInput(query="q"))
        assert result.is_error
