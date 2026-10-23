"""web_search tool - Web search via the Responses API ``web_search`` tool.

References:
    - Rust WebSearchTool (implementations/grok_build/web_search/)
    - design-docs/Python-Agent-补足方案-设计.md §Phase 4

Provider requirement: the configured provider must expose the OpenAI
Responses API with the ``web_search`` tool (e.g. OpenAI). The tool is only
registered when the resolved provider backend is ``RESPONSES``; otherwise it
is absent from the registry so the model never sees a broken tool.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from code_agent.api.schemas import AuthScheme, ProviderConfig
from code_agent.tools.result import ToolResult

_TIMEOUT = 30.0


class WebSearchInput(BaseModel):
    """Input for the web_search tool."""

    query: str = Field(description="The search query to perform.")
    allowed_domains: list[str] | None = Field(
        default=None,
        description="Optional domains to restrict results to (e.g. ['github.com']).",
    )


class WebSearchTool:
    """Web search backed by the Responses API ``web_search`` tool."""

    def __init__(self, config: ProviderConfig):
        self._config = config

    async def run(self, input: WebSearchInput) -> ToolResult:
        """Run a web search and return the synthesized answer + citations.

        Args:
            input: WebSearchInput with query and optional domain filters.

        Returns:
            ToolResult with the answer text and a Sources list, or an error.
        """
        url = f"{self._config.base_url.rstrip('/')}/responses"
        headers = {"Content-Type": "application/json"}
        if self._config.auth_scheme == AuthScheme.X_API_KEY:
            headers["x-api-key"] = self._config.api_key or ""
        else:
            headers["Authorization"] = f"Bearer {self._config.api_key}"

        body: dict[str, Any] = {
            "model": self._config.model,
            "input": input.query,
            "tools": [
                {
                    "type": "web_search",
                    "web_search": {
                        "filters": {"allowed_domains": input.allowed_domains or []},
                    },
                },
            ],
            "store": False,
            "temperature": 0.1,
            "top_p": 0.95,
            "max_output_tokens": 8192,
        }

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            return ToolResult.error(f"Web search request failed: {exc}")

        if response.status_code != 200:
            return ToolResult.error(
                f"Web search HTTP {response.status_code}: {response.text[:500]}",
            )

        return _parse_response(response.json())


def _parse_response(data: dict) -> ToolResult:
    """Extract answer text + deduplicated url_citation sources."""
    content_parts: list[str] = []
    citations: list[str] = []
    seen: set[str] = set()

    for item in data.get("output", []):
        for block in item.get("content", []):
            if block.get("type") == "output_text" and block.get("text"):
                content_parts.append(block["text"])
            for annotation in block.get("annotations", []):
                if annotation.get("type") == "url_citation":
                    url = annotation.get("url", "")
                    if url and url not in seen:
                        seen.add(url)
                        citations.append(url)

    if not content_parts:
        return ToolResult.ok("No search results found.")

    out = "\n".join(content_parts)
    if citations:
        out += "\n\nSources:"
        for i, url in enumerate(citations, 1):
            out += f"\n{i}. {url}"

    return ToolResult.ok(out)
