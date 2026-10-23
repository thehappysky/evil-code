"""web_fetch tool - Fetch URL content and convert HTML to Markdown.

Logic reference: Rust version WebFetchTool
(implementations/grok_build/web_fetch/).

Features:
- HTTP -> HTTPS auto-upgrade
- SSRF 3-layer protection: URL parse -> DNS resolve check -> redirect re-check
- Response truncation: >5MB truncated, return first 256KB + footer
- Content type check: non-HTML returns type info
- HTML -> Markdown conversion via markdownify
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from code_agent.permission.ssrf import (
    _fetch_with_redirect_check,
    _is_private_hostname,
    _is_private_ip,
    _resolve_hostname,
)
from code_agent.prompts.markers import wrap_untrusted
from code_agent.tools.result import ToolResult

_MAX_RESPONSE_SIZE = 5_000_000  # 5MB
_TRUNCATE_SIZE = 256_000  # 256KB
_TIMEOUT = 30.0


class WebFetchInput(BaseModel):
    """Input for web_fetch tool."""

    url: str = Field(description="URL to fetch")
    prompt: str | None = Field(default=None, description="Optional: what to extract from the page")


async def web_fetch(input: WebFetchInput) -> ToolResult:
    """Fetch a URL and return its content as Markdown.

    Args:
        input: WebFetchInput with url and optional prompt.

    Returns:
        ToolResult with Markdown content or error.
    """
    url = input.url.strip()

    # HTTP -> HTTPS upgrade
    if url.startswith("http://"):
        url = "https://" + url[7:]

    if not url.startswith("https://"):
        return ToolResult.error(f"Invalid URL scheme (must be http/https): {url}")

    # SSRF Layer 1: URL parse check
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return ToolResult.error(f"Cannot parse hostname from URL: {url}")

    if _is_private_hostname(hostname):
        return ToolResult.error(
            f"Refused: hostname '{hostname}' resolves to a private/internal address."
        )

    # SSRF Layer 2: DNS resolution check
    try:
        resolved_ips = await _resolve_hostname(hostname)
        for ip in resolved_ips:
            if _is_private_ip(ip):
                return ToolResult.error(
                    f"Refused: '{hostname}' resolves to private IP {ip}."
                )
    except OSError as exc:
        return ToolResult.error(f"DNS resolution failed for '{hostname}': {exc}")

    # Fetch with redirect handling (SSRF Layer 3: re-check redirects)
    try:
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=_TIMEOUT
        ) as client:
            response = await _fetch_with_redirect_check(client, url, max_redirects=5)
    except httpx.HTTPError as exc:
        return ToolResult.error(f"HTTP request failed: {exc}")
    except ValueError as exc:
        return ToolResult.error(str(exc))

    # 5MB response size guard (design doc: "超过 5MB 截断")
    raw_text = response.text
    if len(raw_text) > _MAX_RESPONSE_SIZE:
        raw_text = raw_text[:_MAX_RESPONSE_SIZE]
        # Rebuild a minimal response-like flow with truncated text
        return _truncate_response(
            raw_text + f"\n\n[Response exceeded {_MAX_RESPONSE_SIZE // 1_000_000}MB limit — truncated.]"
        )

    # Content type check
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type and "application/xhtml" not in content_type:
        # Non-HTML: return raw text if text/*, otherwise report type
        if content_type.startswith("text/"):
            return _truncate_response(raw_text)
        return ToolResult.ok(
            f"Content-Type: {content_type}\n"
            f"Cannot convert non-HTML content to Markdown.\n"
            f"URL: {url}\n"
            f"Status: {response.status_code}"
        )

    # HTML -> Markdown
    try:
        from markdownify import markdownify as md_convert

        markdown = md_convert(
            raw_text,
            heading_style="ATX",
            strip=["script", "style", "nav", "footer", "header"],
        )
    except ImportError:
        # Fallback: return raw HTML (truncated)
        markdown = raw_text
    except Exception:
        markdown = raw_text

    # Clean up excessive whitespace
    lines = markdown.splitlines()
    cleaned = []
    blank_count = 0
    for line in lines:
        if not line.strip():
            blank_count += 1
            if blank_count <= 2:
                cleaned.append("")
        else:
            blank_count = 0
            cleaned.append(line)
    markdown = "\n".join(cleaned).strip()

    # Prepend extraction goal if prompt was provided
    if input.prompt:
        markdown = f"[Extraction goal: {input.prompt}]\n\n{markdown}"

    # [Phase 6] 2026-08-04: prompt-injection defense — mark fetched content as
    # untrusted *data*, not instructions. The model should treat everything
    # inside <web_content> as content to analyze, never as commands to obey.
    # [TOOL-03] 2026-08-09: marker logic moved to prompts/markers.py.
    markdown = wrap_untrusted(markdown, source=url)

    return _truncate_response(markdown)


def _truncate_response(text: str) -> ToolResult:
    """Truncate response if too large."""
    if len(text) <= _TRUNCATE_SIZE:
        return ToolResult.ok(text)

    truncated = text[:_TRUNCATE_SIZE]
    truncated += (
        f"\n\n[Content truncated - {len(text):,} characters total, "
        f"showing first {_TRUNCATE_SIZE:,}.]"
    )
    return ToolResult.ok(truncated, truncated=True)
