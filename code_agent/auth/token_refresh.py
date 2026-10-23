"""OAuth2 token refresh - pure functions, no file I/O, no global state.

Logic reference: Rust version AuthManager::try_recover_unauthorized()
"detect expiry -> refresh -> retry" pattern.

Constants (_TOKEN_URL, _CLIENT_ID) reference cc-switch's OAuth parameters
extracted from Claude Code binary.

Dependencies: httpx (already required by openai SDK) + json + time.
Does NOT depend on any project-internal module.
"""

from __future__ import annotations

import time

import httpx

# OAuth2 endpoint and client ID (from cc-switch / Claude Code binary)
_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"


def is_token_expired(token: dict, buffer_seconds: int = 300) -> bool:
    """Check if a token is expired or about to expire.

    Args:
        token: Token dict containing 'expiresAt' (millisecond timestamp).
        buffer_seconds: Safety buffer in seconds before actual expiry.
            Default 300s (5 minutes) to avoid edge-case failures.

    Returns:
        True if token is expired or will expire within buffer_seconds.
    """
    expires_at_ms = token.get("expiresAt")
    if expires_at_ms is None:
        # No expiry info — treat as expired to force refresh
        return True

    expires_at_s = expires_at_ms / 1000.0
    now_s = time.time()
    return now_s >= (expires_at_s - buffer_seconds)


async def refresh_token(
    token: dict,
    timeout: float = 30.0,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Refresh an OAuth2 token using its refresh_token.

    Pure function: does NOT modify the input dict, returns a new dict.
    Caller decides whether to persist the new token.

    Args:
        token: Current token dict (must contain 'refreshToken').
        timeout: HTTP request timeout in seconds.
        client: Optional httpx.AsyncClient for connection reuse.
            If None, a temporary client is created.

    Returns:
        New token dict with updated accessToken, expiresAt,
        and optionally a rotated refreshToken.

    Raises:
        ValueError: If token has no 'refreshToken' field.
        RuntimeError: If HTTP request fails or response is invalid.
    """
    refresh_tok = token.get("refreshToken")
    if not refresh_tok:
        raise ValueError("Token dict missing 'refreshToken' field")

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_tok,
        "client_id": _CLIENT_ID,
    }

    try:
        if client is not None:
            response = await client.post(
                _TOKEN_URL, json=payload, timeout=timeout
            )
        else:
            async with httpx.AsyncClient() as tmp_client:
                response = await tmp_client.post(
                    _TOKEN_URL, json=payload, timeout=timeout
                )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Token refresh HTTP request failed: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(
            f"Token refresh rejected: HTTP {response.status_code} - "
            f"{response.text[:500]}"
        )

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(
            f"Token refresh response is not valid JSON: {exc}"
        ) from exc

    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError(
            "Token refresh response missing 'access_token' field"
        )

    # Build new token dict (immutable pattern — don't modify input)
    expires_in = data.get("expires_in", 3600)
    new_token = {
        **token,
        "accessToken": access_token,
        "expiresAt": int((time.time() + expires_in) * 1000),
    }

    # Server may rotate the refresh token
    new_refresh = data.get("refresh_token")
    if new_refresh:
        new_token["refreshToken"] = new_refresh

    return new_token
