"""Persistent stores for the Evil Code TUI.

- ``JsonUiPreferencesStore``: interface language / effort / mode preferences.
- ``JsonProviderSettingsStore``: user-managed provider profiles.
- ``HttpProviderCatalog``: read-only provider model discovery over HTTP.

These live under ``~/.code-agent`` alongside the rest of the agent's state.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urljoin

import httpx

from code_agent.tui.domain import (
    HttpClientPolicy,
    InteractionMode,
    ManagedProviderProfile,
    ManagedProviderSettings,
    ProviderCatalogError,
    ProviderCatalogResult,
    ProviderConnectionSpec,
    ProviderSettingsError,
    ReasoningEffort,
    UiLanguage,
)

logger = logging.getLogger(__name__)

_DEFAULT_STATE_DIR = Path.home() / ".code-agent"


# ─── UI preferences ───────────────────────────────────────────────────────────


class JsonUiPreferencesStore:
    """Persist interactive user preferences as one small JSON file."""

    def __init__(self, path: Path | None = None):
        self._path = path or _DEFAULT_STATE_DIR / "ui-preferences.json"

    def _read(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def load_language(self) -> UiLanguage:
        try:
            return UiLanguage(
                self._read().get(
                    "language", UiLanguage.SIMPLIFIED_CHINESE.value
                )
            )
        except ValueError:
            return UiLanguage.SIMPLIFIED_CHINESE

    async def save_language(self, language: UiLanguage) -> None:
        data = self._read()
        data["language"] = language.value
        self._write(data)

    async def load_reasoning_effort(self) -> ReasoningEffort:
        try:
            return ReasoningEffort(
                self._read().get("reasoning_effort", ReasoningEffort.HIGH.value)
            )
        except ValueError:
            return ReasoningEffort.HIGH

    async def save_reasoning_effort(self, effort: ReasoningEffort) -> None:
        data = self._read()
        data["reasoning_effort"] = effort.value
        self._write(data)

    async def load_interaction_mode(self) -> InteractionMode:
        try:
            return InteractionMode(
                self._read().get("interaction_mode", InteractionMode.NORMAL.value)
            )
        except ValueError:
            return InteractionMode.NORMAL

    async def save_interaction_mode(self, mode: InteractionMode) -> None:
        data = self._read()
        data["interaction_mode"] = mode.value
        self._write(data)


# ─── Provider settings ────────────────────────────────────────────────────────


class JsonProviderSettingsStore:
    """Persist user-managed provider profiles as one small JSON file."""

    def __init__(self, path: Path | None = None):
        self._path = path or _DEFAULT_STATE_DIR / "profiles.json"

    def _read(self) -> ManagedProviderSettings:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ManagedProviderSettings()
        if not isinstance(data, dict):
            return ManagedProviderSettings()
        profiles: list[ManagedProviderProfile] = []
        for raw in data.get("profiles", []):
            if not isinstance(raw, dict):
                continue
            try:
                profiles.append(ManagedProviderProfile(
                    name=str(raw["name"]),
                    protocol=str(raw["protocol"]),
                    model=str(raw["model"]),
                    base_url=str(raw["base_url"]),
                    dialect=str(raw.get("dialect", "standard")),
                    proxy_mode=str(raw.get("proxy_mode", "environment")),
                    proxy_url_env=(
                        raw["proxy_url_env"] if raw.get("proxy_url_env") else None
                    ),
                    api_key=raw.get("api_key"),
                ))
            except (KeyError, TypeError, ValueError):
                logger.debug("Skipping invalid provider profile", exc_info=True)
                continue
        default = data.get("default_provider")
        if default is not None and not isinstance(default, str):
            default = None
        return ManagedProviderSettings(tuple(profiles), default)

    def _write(self, settings: ManagedProviderSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "default_provider": settings.default_provider,
            "profiles": [
                {
                    "name": profile.name,
                    "protocol": profile.protocol,
                    "model": profile.model,
                    "base_url": profile.base_url,
                    "dialect": profile.dialect,
                    "proxy_mode": profile.proxy_mode,
                    "proxy_url_env": profile.proxy_url_env,
                    "api_key": profile.api_key,
                }
                for profile in settings.profiles
            ],
        }
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def load(self) -> ManagedProviderSettings:
        return self._read()

    async def save_profile(
        self,
        profile: ManagedProviderProfile,
        *,
        make_default: bool = True,
    ) -> ManagedProviderSettings:
        settings = self._read()
        existing = settings.profile(profile.name)
        api_key = profile.api_key
        if api_key is None and existing is not None:
            # A blank API key field keeps the previously saved key.
            api_key = existing.api_key
        stored = ManagedProviderProfile(
            name=profile.name,
            protocol=profile.protocol,
            model=profile.model,
            base_url=profile.base_url,
            dialect=profile.dialect,
            proxy_mode=profile.proxy_mode,
            proxy_url_env=profile.proxy_url_env,
            api_key=api_key,
        )
        profiles = tuple(
            entry for entry in settings.profiles if entry.name != profile.name
        ) + (stored,)
        default = profile.name if make_default else settings.default_provider
        updated = ManagedProviderSettings(profiles, default)
        self._write(updated)
        return updated

    async def set_default(self, name: str) -> ManagedProviderSettings:
        settings = self._read()
        if settings.profile(name) is None:
            raise ProviderSettingsError(f"unknown provider profile: {name}")
        updated = ManagedProviderSettings(settings.profiles, name)
        self._write(updated)
        return updated

    async def delete_profile(self, name: str) -> ManagedProviderSettings:
        settings = self._read()
        profiles = tuple(
            profile for profile in settings.profiles if profile.name != name
        )
        default = settings.default_provider
        if default == name:
            default = profiles[0].name if profiles else None
        updated = ManagedProviderSettings(profiles, default)
        self._write(updated)
        return updated


# ─── Provider model catalog ────────────────────────────────────────────────────

_MAX_MODELS = 200
_RESPONSE_LIMIT = 1_000_000
_TIMEOUT_SECONDS = 15.0


class HttpProviderCatalog:
    """Read-only provider connectivity and model-discovery boundary."""

    def __init__(self, max_models: int = _MAX_MODELS):
        self._max_models = max_models

    @staticmethod
    def _endpoint(spec: ProviderConnectionSpec) -> str:
        base = spec.base_url.rstrip("/")
        if spec.protocol == "gemini-generate-content":
            return urljoin(f"{base}/", "models")
        return urljoin(f"{base}/", "models")

    @staticmethod
    def _headers(spec: ProviderConnectionSpec) -> dict[str, str]:
        if spec.protocol == "anthropic-messages":
            return {
                "x-api-key": spec.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        return {"authorization": f"Bearer {spec.api_key}"}

    async def discover_models(
        self,
        spec: ProviderConnectionSpec,
        *,
        http_policy: HttpClientPolicy,
    ) -> ProviderCatalogResult:
        endpoint = self._endpoint(spec)
        headers = self._headers(spec)
        try:
            timeout = httpx.Timeout(_TIMEOUT_SECONDS)
            async with httpx.AsyncClient(
                **http_policy.client_options(timeout=timeout)
            ) as client:
                response = await client.get(endpoint, headers=headers)
        except httpx.TimeoutException as error:
            raise ProviderCatalogError("timeout", detail=str(error)) from error
        except httpx.ProxyError as error:
            raise ProviderCatalogError("proxy", detail=str(error)) from error
        except httpx.NetworkError as error:
            raise ProviderCatalogError("network", detail=str(error)) from error
        except httpx.HTTPError as error:
            raise ProviderCatalogError("http", detail=str(error)) from error

        if response.status_code in (401, 403):
            raise ProviderCatalogError(
                "authentication", status_code=response.status_code
            )
        if response.status_code == 404:
            raise ProviderCatalogError("endpoint", status_code=response.status_code)
        if response.status_code == 429:
            raise ProviderCatalogError("rate_limit", status_code=response.status_code)
        if response.status_code >= 500:
            raise ProviderCatalogError("server", status_code=response.status_code)
        if response.status_code >= 400:
            raise ProviderCatalogError("http", status_code=response.status_code)

        if len(response.content) > _RESPONSE_LIMIT:
            raise ProviderCatalogError("response_too_large")

        try:
            payload = response.json()
        except json.JSONDecodeError as error:
            raise ProviderCatalogError(
                "invalid_response", detail="provider returned a non-JSON body"
            ) from error

        models = _extract_model_ids(payload)
        if len(models) > self._max_models:
            models = models[: self._max_models]
            return ProviderCatalogResult(tuple(models), truncated=True)
        return ProviderCatalogResult(tuple(models), truncated=False)


def _extract_model_ids(payload: object) -> list[str]:
    """Extract model identifiers from common provider catalog shapes."""
    if not isinstance(payload, dict):
        return []
    models: list[str] = []
    for entry in payload.get("data", ()):
        if isinstance(entry, dict):
            model_id = entry.get("id")
            if isinstance(model_id, str) and model_id:
                models.append(model_id)
    for entry in payload.get("models", ()):
        if isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str) and name:
                models.append(name.split("/")[-1])
    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for model_id in models:
        if model_id not in seen:
            seen.add(model_id)
            unique.append(model_id)
    return unique


__all__ = [
    "HttpProviderCatalog",
    "JsonProviderSettingsStore",
    "JsonUiPreferencesStore",
]
