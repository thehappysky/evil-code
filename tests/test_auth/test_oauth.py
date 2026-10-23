"""Tests for auth layer: token_refresh + token_store.

Covers:
- is_token_expired: expired / valid / no expiry field
- refresh_token: success / missing refresh_token / HTTP error / invalid JSON
- load_token / save_token: atomic write, preserve other keys
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from code_agent.auth.token_refresh import is_token_expired, refresh_token
from code_agent.auth.token_store import load_token, save_token


# ─── is_token_expired ──────────────────────────────────────────────────────────


class TestIsTokenExpired:
    def test_expired_token(self, expired_token: dict):
        """Token past its expiry should be detected as expired."""
        assert is_token_expired(expired_token) is True

    def test_valid_token(self, sample_token: dict):
        """Token with 1h remaining should NOT be expired (default buffer=300s)."""
        assert is_token_expired(sample_token) is False

    def test_within_buffer(self):
        """Token expiring within buffer_seconds should be treated as expired."""
        token = {
            "accessToken": "tok",
            "expiresAt": int((time.time() + 200) * 1000),  # 200s left
        }
        # Default buffer is 300s, so 200s remaining = expired
        assert is_token_expired(token) is True

    def test_no_expiry_field(self):
        """Token without expiresAt should be treated as expired (force refresh)."""
        token = {"accessToken": "tok"}
        assert is_token_expired(token) is True

    def test_custom_buffer(self):
        """Custom buffer_seconds should be respected."""
        token = {
            "accessToken": "tok",
            "expiresAt": int((time.time() + 50) * 1000),  # 50s left
        }
        # With buffer=30, 50s remaining is fine
        assert is_token_expired(token, buffer_seconds=30) is False
        # With buffer=60, 50s remaining is expired
        assert is_token_expired(token, buffer_seconds=60) is True


# ─── refresh_token ─────────────────────────────────────────────────────────────


class TestRefreshToken:
    async def test_success(self, sample_token: dict, httpx_mock):
        """Successful refresh returns new token with updated fields."""
        new_expiry = int((time.time() + 7200) * 1000)
        httpx_mock.add_response(
            url="https://console.anthropic.com/v1/oauth/token",
            json={
                "access_token": "sk-new-access-token",
                "expires_in": 7200,
                "refresh_token": "sk-rotated-refresh-token",
            },
        )

        result = await refresh_token(sample_token)

        assert result["accessToken"] == "sk-new-access-token"
        assert result["refreshToken"] == "sk-rotated-refresh-token"
        assert result["expiresAt"] > sample_token["expiresAt"]
        # Original dict not mutated (immutable pattern)
        assert sample_token["accessToken"] == "sk-test-access-token-12345"

    async def test_success_no_rotation(self, sample_token: dict, httpx_mock):
        """Refresh without token rotation keeps original refreshToken."""
        httpx_mock.add_response(
            url="https://console.anthropic.com/v1/oauth/token",
            json={
                "access_token": "sk-new-access",
                "expires_in": 3600,
            },
        )

        result = await refresh_token(sample_token)

        assert result["accessToken"] == "sk-new-access"
        assert result["refreshToken"] == "sk-test-refresh-token-67890"

    async def test_missing_refresh_token(self):
        """Token without refreshToken raises ValueError."""
        with pytest.raises(ValueError, match="missing 'refreshToken'"):
            await refresh_token({"accessToken": "tok"})

    async def test_http_error(self, sample_token: dict, httpx_mock):
        """HTTP 401 from token endpoint raises RuntimeError."""
        httpx_mock.add_response(
            url="https://console.anthropic.com/v1/oauth/token",
            status_code=401,
            text="Unauthorized",
        )

        with pytest.raises(RuntimeError, match="HTTP 401"):
            await refresh_token(sample_token)

    async def test_invalid_json_response(self, sample_token: dict, httpx_mock):
        """Non-JSON response raises RuntimeError."""
        httpx_mock.add_response(
            url="https://console.anthropic.com/v1/oauth/token",
            text="not json at all",
        )

        with pytest.raises(RuntimeError, match="not valid JSON"):
            await refresh_token(sample_token)

    async def test_missing_access_token_in_response(self, sample_token: dict, httpx_mock):
        """Response without access_token raises RuntimeError."""
        httpx_mock.add_response(
            url="https://console.anthropic.com/v1/oauth/token",
            json={"expires_in": 3600},
        )

        with pytest.raises(RuntimeError, match="missing 'access_token'"):
            await refresh_token(sample_token)


# ─── load_token / save_token ───────────────────────────────────────────────────


class TestTokenStore:
    def test_load_missing_file(self, tmp_path: Path):
        """load_token returns None when credentials file doesn't exist."""
        fake_creds = tmp_path / ".claude" / ".credentials.json"
        with patch("code_agent.auth.token_store._CREDS_FILE", fake_creds):
            assert load_token() is None

    def test_load_invalid_json(self, tmp_path: Path):
        """load_token returns None for malformed JSON."""
        creds_dir = tmp_path / ".claude"
        creds_dir.mkdir(parents=True)
        creds_file = creds_dir / ".credentials.json"
        creds_file.write_text("not valid json {{{", encoding="utf-8")

        with patch("code_agent.auth.token_store._CREDS_FILE", creds_file):
            assert load_token() is None

    def test_load_missing_key(self, tmp_path: Path):
        """load_token returns None when claudeAiOauth key is absent."""
        creds_dir = tmp_path / ".claude"
        creds_dir.mkdir(parents=True)
        creds_file = creds_dir / ".credentials.json"
        creds_file.write_text(
            json.dumps({"otherKey": {"foo": "bar"}}), encoding="utf-8"
        )

        with patch("code_agent.auth.token_store._CREDS_FILE", creds_file):
            assert load_token() is None

    def test_load_valid(self, tmp_path: Path, sample_token: dict):
        """load_token returns token dict for valid credentials."""
        creds_dir = tmp_path / ".claude"
        creds_dir.mkdir(parents=True)
        creds_file = creds_dir / ".credentials.json"
        creds_file.write_text(
            json.dumps({"claudeAiOauth": sample_token}), encoding="utf-8"
        )

        with patch("code_agent.auth.token_store._CREDS_FILE", creds_file):
            result = load_token()
            assert result == sample_token

    def test_save_preserves_other_keys(self, tmp_path: Path, sample_token: dict):
        """save_token preserves other top-level keys in credentials file."""
        creds_dir = tmp_path / ".claude"
        creds_dir.mkdir(parents=True)
        creds_file = creds_dir / ".credentials.json"
        existing = {
            "mcpOAuth": {"server": "data"},
            "claudeAiOauth": {"accessToken": "old"},
        }
        creds_file.write_text(json.dumps(existing), encoding="utf-8")

        with (
            patch("code_agent.auth.token_store._CREDS_FILE", creds_file),
            patch("code_agent.auth.token_store._CREDS_DIR", creds_dir),
        ):
            save_token(sample_token)

        saved = json.loads(creds_file.read_text(encoding="utf-8"))
        assert saved["mcpOAuth"] == {"server": "data"}
        assert saved["claudeAiOauth"] == sample_token

    def test_save_creates_directory(self, tmp_path: Path, sample_token: dict):
        """save_token creates .claude directory if it doesn't exist."""
        creds_dir = tmp_path / ".claude"
        creds_file = creds_dir / ".credentials.json"

        with (
            patch("code_agent.auth.token_store._CREDS_FILE", creds_file),
            patch("code_agent.auth.token_store._CREDS_DIR", creds_dir),
        ):
            save_token(sample_token)

        assert creds_file.exists()
        saved = json.loads(creds_file.read_text(encoding="utf-8"))
        assert saved["claudeAiOauth"]["accessToken"] == "sk-test-access-token-12345"
