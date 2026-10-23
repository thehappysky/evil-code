# [TOOL-02] 2026-08-09: Extracted from tools/builtin/web_fetch.py.
# SSRF (Server-Side Request Forgery) protection is a security-guardrail concern,
# not a tool-layer concern. The private-IP range table and the 3-layer check
# (URL parse -> DNS resolve -> redirect re-check) belong in the Guardrails
# layer (permission/) so other network tools can reuse them.
"""SSRF protection — 3-layer defense against Server-Side Request Forgery.

Layer 1: URL parse check — block obvious private hostnames (localhost, .local,
         raw private IPs).
Layer 2: DNS resolution check — resolve the hostname and block if any resolved
         IP falls in a private/internal range.
Layer 3: Redirect re-check — follow redirects manually, re-applying Layer 1 to
         each redirect target.

These functions were originally inlined in ``tools/builtin/web_fetch.py`` and
are extracted here so any network-touching tool can reuse the same policy.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

# Private/internal IP ranges for SSRF protection
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_private_hostname(hostname: str) -> bool:
    """Check if hostname is obviously private (localhost, .local, etc.)."""
    lower = hostname.lower()
    if lower in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    if lower.endswith(".local") or lower.endswith(".internal"):
        return True
    # Check if it's a raw IP
    try:
        ip = ipaddress.ip_address(hostname)
        return _is_private_ip(ip)
    except ValueError:
        pass
    return False


def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address is in a private/internal range."""
    for network in _PRIVATE_NETWORKS:
        if ip in network:
            return True
    return False


async def _resolve_hostname(hostname: str) -> list:
    """Resolve hostname to IP addresses."""
    loop = asyncio.get_running_loop()
    results = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    ips = set()
    for _, _, _, _, sockaddr in results:
        try:
            ips.add(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            pass
    return list(ips)


async def _fetch_with_redirect_check(
    client: httpx.AsyncClient, url: str, max_redirects: int
) -> httpx.Response:
    """Fetch URL, manually handling redirects with SSRF re-checks."""
    current_url = url
    for _ in range(max_redirects + 1):
        response = await client.get(current_url)

        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location")
            if not location:
                return response

            # Resolve relative redirects
            if location.startswith("/"):
                parsed = urlparse(current_url)
                location = f"{parsed.scheme}://{parsed.netloc}{location}"

            # SSRF Layer 3: re-check redirect target
            redirect_parsed = urlparse(location)
            redirect_host = redirect_parsed.hostname
            if redirect_host and _is_private_hostname(redirect_host):
                raise ValueError(
                    f"Refused: redirect to private hostname '{redirect_host}'"
                )

            current_url = location
            continue

        return response

    raise ValueError(f"Too many redirects (>{max_redirects})")
