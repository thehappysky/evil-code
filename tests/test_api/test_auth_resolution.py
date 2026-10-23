"""Tests for CLI auth resolution: infer_backend_from_url + resolve_provider_config.

Tests the 4-level auth priority chain and backend auto-inference logic.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from code_agent.api.schemas import ApiBackend, AuthScheme
from code_agent.cli.auth import (
    infer_auth_scheme_from_url,
    infer_backend_from_url,
    resolve_provider_config,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_args(**kwargs):
    """Create argparse.Namespace with CLI defaults."""
    defaults = {
        "prompt": None,
        "cwd": ".",
        "model": "claude-sonnet-4-5-20250501",
        "max_turns": 20,
        "base_url": "https://api.anthropic.com/v1",
        "api_key": None,
        "provider": "auto",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ─── infer_backend_from_url ─────────────────────────────────────────────────


class TestInferBackendFromUrl:
    """Test backend auto-inference from base_url string."""

    def test_anthropic_url_inferred_as_messages(self):
        """Anthropic base_url → Messages backend."""
        result = infer_backend_from_url("https://api.anthropic.com/v1")
        assert result == ApiBackend.MESSAGES

    def test_deepseek_url_inferred_as_messages(self):
        """DeepSeek base_url → Messages backend."""
        result = infer_backend_from_url("https://api.deepseek.com/anthropic")
        assert result == ApiBackend.MESSAGES

    def test_openai_url_inferred_as_chat_completions(self):
        """OpenAI base_url → ChatCompletions backend."""
        result = infer_backend_from_url("https://api.openai.com/v1")
        assert result == ApiBackend.CHAT_COMPLETIONS

    def test_xai_url_inferred_as_chat_completions(self):
        """xAI base_url → ChatCompletions backend."""
        result = infer_backend_from_url("https://api.x.ai/v1")
        assert result == ApiBackend.CHAT_COMPLETIONS

    def test_ollama_url_inferred_as_chat_completions(self):
        """Local Ollama base_url → ChatCompletions backend."""
        result = infer_backend_from_url("http://localhost:11434/v1")
        assert result == ApiBackend.CHAT_COMPLETIONS

    def test_empty_url_defaults_to_chat_completions(self):
        """Empty base_url → ChatCompletions (most universal)."""
        result = infer_backend_from_url("")
        assert result == ApiBackend.CHAT_COMPLETIONS

    def test_explicit_provider_overrides_url_inference(self):
        """--provider messages forces Messages even for OpenAI URL."""
        result = infer_backend_from_url("https://api.openai.com/v1", "messages")
        assert result == ApiBackend.MESSAGES

    def test_explicit_chat_completions_provider(self):
        """--provider chat_completions forces ChatCompletions even for Anthropic URL."""
        result = infer_backend_from_url("https://api.anthropic.com/v1", "chat_completions")
        assert result == ApiBackend.CHAT_COMPLETIONS


# ─── infer_auth_scheme_from_url ─────────────────────────────────────────────


class TestInferAuthSchemeFromUrl:
    """Test auth scheme inference from base_url + backend."""

    def test_anthropic_native_url_uses_x_api_key(self):
        """Anthropic native API (api.anthropic.com) + Messages → XApiKey."""
        result = infer_auth_scheme_from_url("https://api.anthropic.com/v1", ApiBackend.MESSAGES)
        assert result == AuthScheme.X_API_KEY

    def test_deepseek_url_uses_bearer(self):
        """DeepSeek URL (not api.anthropic.com) + Messages → Bearer."""
        result = infer_auth_scheme_from_url("https://api.deepseek.com/anthropic", ApiBackend.MESSAGES)
        assert result == AuthScheme.BEARER

    def test_openai_url_uses_bearer(self):
        """OpenAI URL + ChatCompletions → Bearer."""
        result = infer_auth_scheme_from_url("https://api.openai.com/v1", ApiBackend.CHAT_COMPLETIONS)
        assert result == AuthScheme.BEARER

    def test_chat_completions_always_bearer(self):
        """ChatCompletions backend → always Bearer regardless of URL."""
        result = infer_auth_scheme_from_url("https://api.anthropic.com/v1", ApiBackend.CHAT_COMPLETIONS)
        assert result == AuthScheme.BEARER


# ─── resolve_provider_config: Level 1 (CLI --api-key) ───────────────────────


class TestResolveLevel1CliApiKey:
    """Test level 1: CLI --api-key has highest priority."""

    def test_cli_api_key_with_explicit_base_url(self):
        """--api-key + --base-url → uses args.base_url for inference."""
        args = _make_args(
            api_key="sk-test123",
            base_url="https://api.openai.com/v1",
        )
        config = resolve_provider_config(args)
        assert config.api_key == "sk-test123"
        assert config.base_url == "https://api.openai.com/v1"
        assert config.api_backend == ApiBackend.CHAT_COMPLETIONS

    def test_cli_api_key_default_base_url_infers_messages(self):
        """--api-key without --base-url → default (anthropic) → Messages."""
        args = _make_args(api_key="sk-test123")
        config = resolve_provider_config(args)
        assert config.api_key == "sk-test123"
        assert config.api_backend == ApiBackend.MESSAGES

    def test_cli_api_key_with_anthropic_url_uses_x_api_key(self):
        """--api-key + anthropic URL → XApiKey auth scheme."""
        args = _make_args(
            api_key="sk-test123",
            base_url="https://api.anthropic.com/v1",
        )
        config = resolve_provider_config(args)
        assert config.auth_scheme == AuthScheme.X_API_KEY

    def test_cli_api_key_with_openai_url_uses_bearer(self):
        """--api-key + OpenAI URL → Bearer auth scheme."""
        args = _make_args(
            api_key="sk-test123",
            base_url="https://api.openai.com/v1",
        )
        config = resolve_provider_config(args)
        assert config.auth_scheme == AuthScheme.BEARER


# ─── resolve_provider_config: Level 2 (env vars) ────────────────────────────


class TestResolveLevel2EnvVars:
    """Test level 2: environment variables."""

    def test_env_var_openai_url_infers_chat_completions(self):
        """BUG FIX: env ANTHROPIC_BASE_URL=OpenAI → ChatCompletions (not Messages)."""
        args = _make_args()
        with patch.dict(
            "os.environ",
            {
                "ANTHROPIC_AUTH_TOKEN": "sk-env-test",
                "ANTHROPIC_BASE_URL": "https://api.openai.com/v1",
                "ANTHROPIC_MODEL": "gpt-4o",
            },
        ):
            config = resolve_provider_config(args)
        assert config.api_key == "sk-env-test"
        assert config.base_url == "https://api.openai.com/v1"
        assert config.api_backend == ApiBackend.CHAT_COMPLETIONS  # BUG FIX
        assert config.auth_scheme == AuthScheme.BEARER
        assert config.model == "gpt-4o"

    def test_env_var_deepseek_url_infers_messages(self):
        """Env ANTHROPIC_BASE_URL=DeepSeek → Messages backend."""
        args = _make_args()
        with patch.dict(
            "os.environ",
            {
                "ANTHROPIC_AUTH_TOKEN": "sk-env-test",
                "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
                "ANTHROPIC_MODEL": "deepseek-v4-pro",
            },
        ):
            config = resolve_provider_config(args)
        assert config.api_backend == ApiBackend.MESSAGES
        assert config.base_url == "https://api.deepseek.com/anthropic"

    def test_env_api_key_uses_x_api_key_scheme(self):
        """ANTHROPIC_API_KEY (not AUTH_TOKEN) → XApiKey."""
        args = _make_args()
        with patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "sk-env-test",
                "ANTHROPIC_BASE_URL": "https://api.anthropic.com/v1",
            },
            clear=False,
        ):
            # Remove AUTH_TOKEN if present to ensure API_KEY is used
            import os
            if "ANTHROPIC_AUTH_TOKEN" in os.environ:
                del os.environ["ANTHROPIC_AUTH_TOKEN"]
            config = resolve_provider_config(args)
        assert config.auth_scheme == AuthScheme.X_API_KEY


# ─── resolve_provider_config: Level 3 (settings.json) ───────────────────────


class TestResolveLevel3SettingsJson:
    """Test level 3: settings.json (cc-switch integration)."""

    def test_settings_json_openai_url_infers_chat_completions(self, tmp_path, monkeypatch):
        """BUG FIX: settings.json with OpenAI base_url → ChatCompletions (not Messages)."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "env": {
                "ANTHROPIC_AUTH_TOKEN": "sk-settings-test",
                "ANTHROPIC_BASE_URL": "https://api.openai.com/v1",
                "ANTHROPIC_MODEL": "gpt-4o",
            }
        }))
        monkeypatch.setattr(
            "code_agent.auth.settings_store._DEFAULT_SETTINGS_PATH",
            settings_file,
        )
        args = _make_args()
        # Clear env vars to ensure settings.json is used
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        config = resolve_provider_config(args)
        assert config.api_key == "sk-settings-test"
        assert config.base_url == "https://api.openai.com/v1"
        assert config.api_backend == ApiBackend.CHAT_COMPLETIONS  # BUG FIX
        assert config.auth_scheme == AuthScheme.BEARER

    def test_settings_json_deepseek_url_infers_messages(self, tmp_path, monkeypatch):
        """settings.json with DeepSeek base_url → Messages backend."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "env": {
                "ANTHROPIC_AUTH_TOKEN": "sk-settings-test",
                "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
                "ANTHROPIC_MODEL": "deepseek-v4-pro",
            }
        }))
        monkeypatch.setattr(
            "code_agent.auth.settings_store._DEFAULT_SETTINGS_PATH",
            settings_file,
        )
        args = _make_args()
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        config = resolve_provider_config(args)
        assert config.api_backend == ApiBackend.MESSAGES
        assert config.model == "deepseek-v4-pro"

    def test_settings_json_anthropic_url_uses_bearer(self, tmp_path, monkeypatch):
        """settings.json with Anthropic base_url + AUTH_TOKEN → Bearer (not XApiKey)."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "env": {
                "ANTHROPIC_AUTH_TOKEN": "sk-settings-test",
                "ANTHROPIC_BASE_URL": "https://api.anthropic.com/v1",
                "ANTHROPIC_MODEL": "claude-sonnet-4-5-20250501",
            }
        }))
        monkeypatch.setattr(
            "code_agent.auth.settings_store._DEFAULT_SETTINGS_PATH",
            settings_file,
        )
        args = _make_args()
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        config = resolve_provider_config(args)
        # AUTH_TOKEN → Bearer (even for Anthropic URL, because it's AUTH_TOKEN not API_KEY)
        assert config.auth_scheme == AuthScheme.BEARER
        assert config.api_backend == ApiBackend.MESSAGES


# ─── resolve_provider_config: Priority chain ─────────────────────────────────


class TestResolvePriorityChain:
    """Test that higher-priority levels override lower ones."""

    def test_cli_api_key_overrides_env_vars(self, monkeypatch):
        """Level 1 (--api-key) takes priority over level 2 (env vars)."""
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-env-should-be-ignored")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.openai.com/v1")
        args = _make_args(
            api_key="sk-cli-wins",
            base_url="https://api.deepseek.com/anthropic",
        )
        config = resolve_provider_config(args)
        assert config.api_key == "sk-cli-wins"
        assert config.base_url == "https://api.deepseek.com/anthropic"

    def test_env_vars_override_settings_json(self, tmp_path, monkeypatch):
        """Level 2 (env vars) takes priority over level 3 (settings.json)."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "env": {
                "ANTHROPIC_AUTH_TOKEN": "sk-settings-ignored",
                "ANTHROPIC_BASE_URL": "https://api.openai.com/v1",
            }
        }))
        monkeypatch.setattr(
            "code_agent.auth.settings_store._DEFAULT_SETTINGS_PATH",
            settings_file,
        )
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-env-wins")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")

        args = _make_args()
        config = resolve_provider_config(args)
        assert config.api_key == "sk-env-wins"
        assert config.base_url == "https://api.deepseek.com/anthropic"

    def test_provider_flag_overrides_auto_inference(self, tmp_path, monkeypatch):
        """--provider messages forces Messages even for OpenAI base_url in settings."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "env": {
                "ANTHROPIC_AUTH_TOKEN": "sk-test",
                "ANTHROPIC_BASE_URL": "https://api.openai.com/v1",
            }
        }))
        monkeypatch.setattr(
            "code_agent.auth.settings_store._DEFAULT_SETTINGS_PATH",
            settings_file,
        )
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        args = _make_args(provider="messages")
        config = resolve_provider_config(args)
        assert config.api_backend == ApiBackend.MESSAGES  # forced by --provider
