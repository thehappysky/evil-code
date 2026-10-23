"""Tests for auth/settings_store.py: settings.json env block reader.

Covers:
- File not found -> is_present=False
- Invalid JSON -> is_present=False
- ANTHROPIC_AUTH_TOKEN (Bearer) priority
- ANTHROPIC_API_KEY (XApiKey) fallback
- Both present -> AUTH_TOKEN wins
- Neither present -> is_present=False
- Custom model + base_url extraction
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_agent.api.schemas import AuthScheme
from code_agent.auth.settings_store import load_settings_credentials


def _write_settings(path: Path, env: dict, **extra) -> Path:
    """Write a settings.json file with given env block."""
    data = {"env": env, **extra}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestSettingsStore:
    async def test_file_not_found(self, tmp_path: Path):
        """Missing settings.json returns is_present=False."""
        result = load_settings_credentials(tmp_path / "missing.json")
        assert not result.is_present
        assert result.api_key is None

    async def test_invalid_json(self, tmp_path: Path):
        """Invalid JSON returns is_present=False."""
        path = tmp_path / "settings.json"
        path.write_text("not valid json {{{", encoding="utf-8")
        result = load_settings_credentials(path)
        assert not result.is_present

    async def test_auth_token_bearer(self, tmp_path: Path):
        """ANTHROPIC_AUTH_TOKEN → Bearer scheme."""
        path = _write_settings(
            tmp_path / "settings.json",
            {
                "ANTHROPIC_AUTH_TOKEN": "sk-auth-123",
                "ANTHROPIC_BASE_URL": "https://api.test.com",
                "ANTHROPIC_MODEL": "test-model",
            },
        )
        result = load_settings_credentials(path)
        assert result.is_present
        assert result.api_key == "sk-auth-123"
        assert result.auth_scheme == AuthScheme.BEARER
        assert result.base_url == "https://api.test.com"
        assert result.model == "test-model"

    async def test_api_key_xapikey(self, tmp_path: Path):
        """ANTHROPIC_API_KEY → XApiKey scheme."""
        path = _write_settings(
            tmp_path / "settings.json",
            {"ANTHROPIC_API_KEY": "sk-key-456"},
        )
        result = load_settings_credentials(path)
        assert result.is_present
        assert result.api_key == "sk-key-456"
        assert result.auth_scheme == AuthScheme.X_API_KEY

    async def test_auth_token_priority(self, tmp_path: Path):
        """When both present, ANTHROPIC_AUTH_TOKEN wins (Bearer)."""
        path = _write_settings(
            tmp_path / "settings.json",
            {
                "ANTHROPIC_AUTH_TOKEN": "sk-token",
                "ANTHROPIC_API_KEY": "sk-key",
            },
        )
        result = load_settings_credentials(path)
        assert result.api_key == "sk-token"
        assert result.auth_scheme == AuthScheme.BEARER

    async def test_neither_present(self, tmp_path: Path):
        """No auth env vars → is_present=False."""
        path = _write_settings(
            tmp_path / "settings.json",
            {"ANTHROPIC_BASE_URL": "https://api.test.com"},
        )
        result = load_settings_credentials(path)
        assert not result.is_present

    async def test_extra_keys_preserved(self, tmp_path: Path):
        """Other settings.json keys (permissions, theme) don't break parsing."""
        path = _write_settings(
            tmp_path / "settings.json",
            {"ANTHROPIC_AUTH_TOKEN": "sk-123"},
            permissions={"allow": []},
            theme="dark",
        )
        result = load_settings_credentials(path)
        assert result.is_present
        assert result.api_key == "sk-123"
