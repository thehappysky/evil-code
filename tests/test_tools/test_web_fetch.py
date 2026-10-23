"""Tests for the web_fetch built-in tool.

Covers:
- Invalid scheme rejection (ftp, javascript, etc.)
- HTTP -> HTTPS auto-upgrade
- SSRF layer 1: obvious private hostnames (localhost, 127.0.0.1, *.local)
  rejected before any network call
- SSRF layer 2: DNS resolution returns a private IP
- SSRF layer 3: redirect target re-check rejects private hostnames
- Success path: HTML -> Markdown (strips script/style tags)
- Non-HTML content type (application/json) -> Content-Type note
- Truncation of responses larger than 5MB
- DNS resolution failure

All DNS lookups are stubbed by monkeypatching ``_resolve_hostname`` and all
HTTP responses are mocked via pytest-httpx's ``httpx_mock`` fixture, so no
real network access ever occurs.
"""

from __future__ import annotations

import ipaddress

import pytest

from code_agent.tools.builtin.web_fetch import WebFetchInput, web_fetch

# A public IP used to satisfy SSRF layer 2 on happy-path tests.
_PUBLIC_IP = ipaddress.ip_address("93.184.216.34")


async def _resolve_public(hostname: str) -> list:
    """Fake resolver: always returns a single public IP."""
    return [_PUBLIC_IP]


@pytest.fixture(autouse=True)
def _mock_dns(monkeypatch):
    """Stub real DNS so tests never touch the network."""
    monkeypatch.setattr(
        "code_agent.tools.builtin.web_fetch._resolve_hostname",
        _resolve_public,
    )


# ─── Scheme validation ─────────────────────────────────────────────────────────


class TestSchemeValidation:
    async def test_ftp_scheme_rejected(self):
        """ftp:// URLs are rejected as an invalid scheme."""
        result = await web_fetch(WebFetchInput(url="ftp://example.com/file"))
        assert result.is_error is True
        assert "scheme" in result.output.lower()

    async def test_javascript_scheme_rejected(self):
        """javascript: URLs are rejected as an invalid scheme."""
        result = await web_fetch(WebFetchInput(url="javascript:alert(1)"))
        assert result.is_error is True
        assert "scheme" in result.output.lower()

    async def test_bare_string_rejected(self):
        """A non-URL string is rejected."""
        result = await web_fetch(WebFetchInput(url="just some words"))
        assert result.is_error is True
        assert "scheme" in result.output.lower()


# ─── HTTP -> HTTPS auto-upgrade ────────────────────────────────────────────────


class TestHttpToHttpsUpgrade:
    async def test_http_upgrades_to_https(self, httpx_mock):
        """http:// is auto-upgraded to https:// and fetched over HTTPS."""
        httpx_mock.add_response(
            url="https://example.com",
            html="<html><body><p>upgraded ok</p></body></html>",
        )
        result = await web_fetch(WebFetchInput(url="http://example.com"))
        assert result.is_error is False
        assert "upgraded ok" in result.output


# ─── SSRF layer 1: hostname checks ─────────────────────────────────────────────


class TestSsrfLayer1Hostname:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost",
            "http://127.0.0.1",
            "http://foo.local",
        ],
    )
    async def test_private_hostname_rejected(self, url):
        """Obvious private hostnames are refused before any network call."""
        result = await web_fetch(WebFetchInput(url=url))
        assert result.is_error is True
        assert "private" in result.output.lower()


# ─── SSRF layer 2: DNS resolution check ────────────────────────────────────────


class TestSsrfLayer2Dns:
    async def test_private_ip_after_resolution(self, monkeypatch):
        """A hostname resolving to a private IP is refused."""

        async def _resolve_private(hostname: str) -> list:
            return [ipaddress.ip_address("127.0.0.1")]

        monkeypatch.setattr(
            "code_agent.tools.builtin.web_fetch._resolve_hostname",
            _resolve_private,
        )
        result = await web_fetch(WebFetchInput(url="https://example.com"))
        assert result.is_error is True
        assert "private" in result.output.lower()

    async def test_dns_failure_returns_error(self, monkeypatch):
        """A failed DNS lookup returns an error result."""

        async def _resolve_fail(hostname: str) -> list:
            raise OSError("no such host")

        monkeypatch.setattr(
            "code_agent.tools.builtin.web_fetch._resolve_hostname",
            _resolve_fail,
        )
        result = await web_fetch(WebFetchInput(url="https://example.com"))
        assert result.is_error is True
        assert "dns" in result.output.lower()


# ─── SSRF layer 3: redirect re-check ───────────────────────────────────────────


class TestSsrfLayer3Redirect:
    async def test_redirect_to_private_hostname_rejected(self, httpx_mock):
        """A redirect to a private hostname is refused after the fetch."""
        httpx_mock.add_response(
            url="https://example.com",
            status_code=302,
            headers={"location": "http://127.0.0.1/secret"},
        )
        result = await web_fetch(WebFetchInput(url="https://example.com"))
        assert result.is_error is True
        assert "redirect" in result.output.lower()

    async def test_redirect_to_public_hostname_followed(self, httpx_mock):
        """A redirect to a public hostname is followed to completion."""
        httpx_mock.add_response(
            url="https://example.com",
            status_code=302,
            headers={"location": "https://example.org/final"},
        )
        httpx_mock.add_response(
            url="https://example.org/final",
            html="<html><body><p>redirected body</p></body></html>",
        )
        result = await web_fetch(WebFetchInput(url="https://example.com"))
        assert result.is_error is False
        assert "redirected body" in result.output


# ─── Success path ──────────────────────────────────────────────────────────────


class TestSuccessPath:
    async def test_html_converted_to_markdown(self, httpx_mock):
        """HTML pages are converted to Markdown with script/style stripped."""
        html = (
            "<html><head>"
            "<script>alert('xss')</script>"
            "<style>.x{color:red}</style>"
            "</head><body>"
            "<h1>Hello World</h1>"
            "<p>Some real content here.</p>"
            "</body></html>"
        )
        httpx_mock.add_response(url="https://example.com", html=html)
        result = await web_fetch(WebFetchInput(url="https://example.com"))
        assert result.is_error is False
        assert "Hello World" in result.output
        assert "Some real content here" in result.output
        assert "<script" not in result.output.lower()
        assert "<style" not in result.output.lower()

    async def test_fetched_content_marked_untrusted(self, httpx_mock):
        """Fetched content is wrapped as untrusted data (injection defense)."""
        httpx_mock.add_response(
            url="https://example.com",
            html="<html><body><p>Hello</p></body></html>",
        )
        result = await web_fetch(WebFetchInput(url="https://example.com"))
        assert result.is_error is False
        assert "<web_content" in result.output
        assert "untrusted-data" in result.output
        assert "</web_content>" in result.output


# ─── Content-type handling ─────────────────────────────────────────────────────


class TestContentType:
    async def test_non_html_reports_content_type(self, httpx_mock):
        """application/json responses are not converted, type is reported."""
        httpx_mock.add_response(
            url="https://example.com/data.json",
            json={"key": "value"},
        )
        result = await web_fetch(WebFetchInput(url="https://example.com/data.json"))
        assert result.is_error is False
        assert "application/json" in result.output
        assert "Content-Type" in result.output


# ─── Truncation ────────────────────────────────────────────────────────────────


class TestTruncation:
    async def test_large_response_truncated(self, httpx_mock):
        """Responses larger than 5MB are truncated and flagged."""
        big_body = "<p>" + ("x" * 5_000_100) + "</p>"
        httpx_mock.add_response(url="https://example.com/big", html=big_body)
        result = await web_fetch(WebFetchInput(url="https://example.com/big"))
        assert result.is_error is False
        assert result.truncated is True
