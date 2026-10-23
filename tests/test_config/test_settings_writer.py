"""Tests for config/settings_writer.py -- save/show/clear settings.json.

Tests the "parameter -> config file" flow (cc-switch equivalent).
"""

import json
from pathlib import Path

import pytest

from code_agent.config.settings_writer import (
    ConfigSummary,
    _mask_key,
    clear_settings_config,
    save_settings_config,
    show_settings_config,
)


@pytest.fixture
def settings_path(tmp_path: Path) -> Path:
    """Create a settings.json with non-env keys to verify preservation."""
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"model": "test-model", "theme": "dark"}),
        encoding="utf-8",
    )
    return path


# ─── Save tests ────────────────────────────────────────────────────────────────


class TestSaveSettingsConfig:
    def test_save_bearer_auth(self, settings_path: Path):
        result = save_settings_config(
            api_key="sk-test123",
            base_url="https://api.deepseek.com/anthropic",
            model="deepseek-v4-pro",
            use_auth_token=True,
            settings_path=settings_path,
        )
        assert result == settings_path
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        env = data["env"]
        assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test123"
        assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
        assert env["ANTHROPIC_MODEL"] == "deepseek-v4-pro"
        assert "ANTHROPIC_API_KEY" not in env

    def test_save_x_api_key_auth(self, settings_path: Path):
        save_settings_config(
            api_key="sk-test456",
            base_url="https://api.anthropic.com",
            model="claude-sonnet-4",
            use_auth_token=False,
            settings_path=settings_path,
        )
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        env = data["env"]
        assert env["ANTHROPIC_API_KEY"] == "sk-test456"
        assert "ANTHROPIC_AUTH_TOKEN" not in env

    def test_save_preserves_non_env_keys(self, settings_path: Path):
        save_settings_config(
            api_key="sk-test",
            base_url="https://api.test.com",
            model="test-model",
            settings_path=settings_path,
        )
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert data["theme"] == "dark"
        assert data["model"] == "test-model"

    def test_save_overwrites_previous_auth(self, settings_path: Path):
        # Save once with bearer
        save_settings_config(
            api_key="sk-first",
            base_url="https://api.test.com",
            model="model-a",
            use_auth_token=True,
            settings_path=settings_path,
        )
        # Save again with x_api_key -- should clear AUTH_TOKEN
        save_settings_config(
            api_key="sk-second",
            base_url="https://api.test.com",
            model="model-b",
            use_auth_token=False,
            settings_path=settings_path,
        )
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        env = data["env"]
        assert env["ANTHROPIC_API_KEY"] == "sk-second"
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        assert env["ANTHROPIC_MODEL"] == "model-b"

    def test_save_creates_parent_dir(self, tmp_path: Path):
        path = tmp_path / "subdir" / "settings.json"
        save_settings_config(
            api_key="sk-test",
            base_url="https://api.test.com",
            model="test",
            settings_path=path,
        )
        assert path.exists()


# ─── Show tests ────────────────────────────────────────────────────────────────


class TestShowSettingsConfig:
    def test_show_with_bearer_config(self, settings_path: Path):
        save_settings_config(
            api_key="sk-showtest",
            base_url="https://api.deepseek.com/anthropic",
            model="deepseek-v4-pro",
            use_auth_token=True,
            settings_path=settings_path,
        )
        summary = show_settings_config(settings_path)
        assert summary.api_key == "sk-showtest"
        assert summary.base_url == "https://api.deepseek.com/anthropic"
        assert summary.model == "deepseek-v4-pro"
        assert summary.auth_token_present is True
        assert summary.api_key_present is False
        assert summary.is_configured is True
        assert summary.settings_path == settings_path

    def test_show_with_x_api_key_config(self, settings_path: Path):
        save_settings_config(
            api_key="sk-keytest",
            base_url="https://api.anthropic.com",
            model="claude-sonnet-4",
            use_auth_token=False,
            settings_path=settings_path,
        )
        summary = show_settings_config(settings_path)
        assert summary.api_key == "sk-keytest"
        assert summary.auth_token_present is False
        assert summary.api_key_present is True
        assert summary.is_configured is True

    def test_show_empty_settings(self, tmp_path: Path):
        path = tmp_path / "empty.json"
        path.write_text("{}", encoding="utf-8")
        summary = show_settings_config(path)
        assert summary.api_key is None
        assert summary.base_url is None
        assert summary.model is None
        assert summary.is_configured is False
        assert summary.auth_token_present is False
        assert summary.api_key_present is False

    def test_show_missing_file(self, tmp_path: Path):
        path = tmp_path / "nonexistent.json"
        summary = show_settings_config(path)
        assert summary.is_configured is False
        assert summary.api_key is None


# ─── Clear tests ───────────────────────────────────────────────────────────────


class TestClearSettingsConfig:
    def test_clear_removes_auth_keys(self, settings_path: Path):
        save_settings_config(
            api_key="sk-clearme",
            base_url="https://api.test.com",
            model="test-model",
            settings_path=settings_path,
        )
        clear_settings_config(settings_path)
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        env = data.get("env", {})
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_BASE_URL" not in env
        assert "ANTHROPIC_MODEL" not in env

    def test_clear_preserves_non_env_keys(self, settings_path: Path):
        save_settings_config(
            api_key="sk-clearme",
            base_url="https://api.test.com",
            model="test-model",
            settings_path=settings_path,
        )
        clear_settings_config(settings_path)
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert data.get("theme") == "dark"
        assert data.get("model") == "test-model"

    def test_clear_removes_empty_env_block(self, settings_path: Path):
        save_settings_config(
            api_key="sk-clearme",
            base_url="https://api.test.com",
            model="test-model",
            settings_path=settings_path,
        )
        clear_settings_config(settings_path)
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "env" not in data

    def test_clear_on_missing_file(self, tmp_path: Path):
        path = tmp_path / "nonexistent.json"
        result = clear_settings_config(path)
        assert result == path
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == {}


# ─── Helper tests ──────────────────────────────────────────────────────────────


class TestMaskKey:
    def test_mask_long_key(self):
        result = _mask_key("sk-1234567890abcdef")
        assert result == "sk-12345...cdef"

    def test_mask_short_key(self):
        result = _mask_key("short")
        assert result == "***"

    def test_mask_empty_key(self):
        result = _mask_key("")
        assert result == ""

    def test_mask_exact_12_chars(self):
        result = _mask_key("sk-1234567890")
        assert result == "sk-12345...7890"
