"""Provider/auth resolution — 5-level priority chain + resilient client.

Extracted from ``cli.main`` to isolate auth concerns (backend inference,
OAuth refresh, circuit-breaker client construction) from CLI assembly.

Auth resolution order:
    1. CLI args (--api-key, --base-url, --model)
    2. ~/.evil-code/config.yaml (evil-code native, auto-login)
    3. Environment variables (ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY)
    4. ~/.claude/settings.json env block (cc-switch managed)
    5. ~/.claude/.credentials.json OAuth token (claude login)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from code_agent.api.schemas import ApiBackend, AuthScheme, ProviderConfig
from code_agent.auth import (
    is_token_expired,
    load_config,
    load_settings_credentials,
    load_token,
    refresh_token,
    save_token,
)
from code_agent.core.exceptions import AuthenticationError

logger = logging.getLogger(__name__)


def infer_backend_from_url(base_url: str, provider: str = "auto") -> ApiBackend:
    """Infer API backend protocol from --provider or base_url string."""
    if provider == "chat_completions":
        return ApiBackend.CHAT_COMPLETIONS
    elif provider == "messages":
        return ApiBackend.MESSAGES
    elif provider == "responses":
        return ApiBackend.RESPONSES
    else:
        url = (base_url or "").lower()
        if "anthropic" in url or "deepseek" in url:
            return ApiBackend.MESSAGES
        return ApiBackend.CHAT_COMPLETIONS


def infer_backend(args: argparse.Namespace) -> ApiBackend:
    """Infer backend from argparse args (uses args.base_url)."""
    return infer_backend_from_url(
        args.base_url or "", getattr(args, "provider", "auto")
    )


def infer_auth_scheme_from_url(base_url: str, backend: ApiBackend) -> AuthScheme:
    """Infer auth scheme from backend + base_url string."""
    url = (base_url or "").lower()
    if backend == ApiBackend.MESSAGES and "api.anthropic.com" in url:
        return AuthScheme.X_API_KEY
    return AuthScheme.BEARER


def infer_auth_scheme(args: argparse.Namespace) -> AuthScheme:
    """Infer auth scheme from argparse args."""
    return infer_auth_scheme_from_url(args.base_url or "", infer_backend(args))


async def resolve_oauth_config(args: argparse.Namespace) -> ProviderConfig:
    """Resolve config from OAuth token (level 5 fallback)."""
    token = load_token()
    if token is None:
        raise AuthenticationError(
            "No API key found. Use --api-key, set ANTHROPIC_API_KEY env var, "
            "configure ~/.claude/settings.json, or run 'claude login' for OAuth."
        )

    if is_token_expired(token):
        try:
            token = await refresh_token(token)
            save_token(token)
        except (ValueError, RuntimeError) as exc:
            raise AuthenticationError(f"Token refresh failed: {exc}") from exc

    access_token = token.get("accessToken")
    if not access_token:
        raise AuthenticationError("Token missing accessToken field")

    return ProviderConfig(
        api_backend=ApiBackend.CHAT_COMPLETIONS,
        base_url=args.base_url,
        api_key=access_token,
        auth_scheme=AuthScheme.BEARER,
        model=args.model,
    )


def resolve_provider_config(args: argparse.Namespace) -> ProviderConfig:
    """Resolve auth config via 5-level priority chain."""
    provider = getattr(args, "provider", "auto")

    # 1. CLI --api-key (highest priority)
    if args.api_key:
        return ProviderConfig(
            api_backend=infer_backend(args),
            base_url=args.base_url,
            api_key=args.api_key,
            auth_scheme=infer_auth_scheme(args),
            model=args.model,
        )

    # 2. ~/.evil-code/config.yaml
    evil_config = load_config()
    if evil_config is not None and evil_config.is_present():
        cfg_base_url = evil_config.base_url or args.base_url
        cfg_backend_str = evil_config.api_backend or "auto"
        cfg_auth_str = evil_config.auth_scheme or "bearer"
        return ProviderConfig(
            api_backend=infer_backend_from_url(cfg_base_url, cfg_backend_str),
            base_url=cfg_base_url,
            api_key=evil_config.api_key,
            auth_scheme=(
                AuthScheme.X_API_KEY if cfg_auth_str == "x_api_key"
                else AuthScheme.BEARER
            ),
            model=evil_config.model or args.model,
            max_tokens=evil_config.max_tokens or 4096,
            temperature=evil_config.temperature or 0.7,
        )

    # 3. Environment variables
    env_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    env_api_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_token or env_api_key:
        api_key = env_token or env_api_key
        auth_scheme = AuthScheme.BEARER if env_token else AuthScheme.X_API_KEY
        env_base_url = os.environ.get("ANTHROPIC_BASE_URL", args.base_url)
        return ProviderConfig(
            api_backend=infer_backend_from_url(env_base_url, provider),
            base_url=env_base_url,
            api_key=api_key,
            auth_scheme=auth_scheme,
            model=os.environ.get("ANTHROPIC_MODEL", args.model),
        )

    # 4. settings.json (cc-switch integration)
    settings_creds = load_settings_credentials()
    if settings_creds.is_present:
        settings_base_url = settings_creds.base_url or args.base_url
        return ProviderConfig(
            api_backend=infer_backend_from_url(settings_base_url, provider),
            base_url=settings_base_url,
            api_key=settings_creds.api_key,
            auth_scheme=settings_creds.auth_scheme or AuthScheme.BEARER,
            model=settings_creds.model or args.model,
        )

    # 5. OAuth token
    return asyncio.run(resolve_oauth_config(args))


async def oauth_refresh_cb() -> str | None:
    """Refresh the OAuth token at runtime on 401 (best-effort).

    Returns the new access token, or None when OAuth is unavailable (e.g.
    plain API-key auth) or the refresh fails — the caller then surfaces the
    AuthenticationError as before.
    """
    token = load_token()
    if not token:
        return None
    try:
        if is_token_expired(token):
            token = await refresh_token(token)
            save_token(token)
        return token.get("accessToken")
    except Exception:
        logger.warning("Runtime OAuth refresh failed", exc_info=True)
        return None


def config_from_profile(profile) -> ProviderConfig:
    """Build a ProviderConfig from a managed provider profile."""
    backend = {
        "openai-chat": ApiBackend.CHAT_COMPLETIONS,
        "openai-responses": ApiBackend.RESPONSES,
        "anthropic-messages": ApiBackend.MESSAGES,
        "gemini-generate-content": ApiBackend.CHAT_COMPLETIONS,
    }.get(profile.protocol, ApiBackend.CHAT_COMPLETIONS)
    auth_scheme = (
        AuthScheme.X_API_KEY
        if backend is ApiBackend.MESSAGES and "api.anthropic.com" in profile.base_url
        else AuthScheme.BEARER
    )
    return ProviderConfig(
        api_backend=backend,
        base_url=profile.base_url,
        api_key=profile.api_key,
        auth_scheme=auth_scheme,
        model=profile.model,
    )
