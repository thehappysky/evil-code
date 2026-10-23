"""Tests for code_agent/auth/config_store.py.

Exercises the real save_config/load_config/generate_config against the
actual ~/.evil-code/config.yaml file format (YAML), using tmp_path so the
real home directory is never touched.

These tests are marked ``use_real_config`` to bypass the autouse
``mock_no_config_file`` fixture that otherwise stubs load_config to None.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from code_agent.auth.config_store import (
    EvilCodeConfig,
    generate_config,
    load_config,
    save_config,
)


@pytest.mark.use_real_config
class TestConfigStore:
    # ─── save_config -> load_config round-trip ─────────────────────────────────

    def test_round_trip_persists_key_fields(self, tmp_path: Path):
        path = tmp_path / "config.yaml"
        config = generate_config(
            api_key="sk-roundtrip",
            base_url="https://api.deepseek.com/anthropic",
            model="deepseek-v4-pro",
        )
        save_config(config, path)
        loaded = load_config(path)
        assert loaded is not None
        assert loaded.api_key == "sk-roundtrip"
        assert loaded.base_url == "https://api.deepseek.com/anthropic"
        assert loaded.model == "deepseek-v4-pro"

    def test_round_trip_all_fields(self, tmp_path: Path):
        path = tmp_path / "config.yaml"
        config = EvilCodeConfig(
            api_backend="messages",
            base_url="https://api.anthropic.com",
            api_key="sk-all",
            auth_scheme="x_api_key",
            model="claude-sonnet-4",
            max_tokens=2048,
            temperature=0.3,
        )
        save_config(config, path)
        loaded = load_config(path)
        assert loaded is not None
        assert loaded.api_backend == "messages"
        assert loaded.base_url == "https://api.anthropic.com"
        assert loaded.api_key == "sk-all"
        assert loaded.auth_scheme == "x_api_key"
        assert loaded.model == "claude-sonnet-4"
        assert loaded.max_tokens == 2048
        assert loaded.temperature == 0.3

    def test_round_trip_written_as_yaml(self, tmp_path: Path):
        path = tmp_path / "config.yaml"
        save_config(
            generate_config(api_key="sk-yaml", base_url="https://x.test.com"),
            path,
        )
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert data["provider"]["api_key"] == "sk-yaml"
        assert data["provider"]["base_url"] == "https://x.test.com"

    def test_save_omits_unset_fields(self, tmp_path: Path):
        path = tmp_path / "config.yaml"
        save_config(EvilCodeConfig(api_key="sk-min", base_url="https://x.test.com"), path)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["provider"]["api_key"] == "sk-min"
        assert data["provider"]["base_url"] == "https://x.test.com"
        assert "model" not in data["provider"]
        assert "max_tokens" not in data["provider"]
        assert "temperature" not in data["provider"]

    def test_save_creates_parent_dir(self, tmp_path: Path):
        path = tmp_path / "sub" / "dir" / "config.yaml"
        save_config(generate_config(api_key="sk-dir"), path)
        assert path.exists()
        assert load_config(path) is not None

    # ─── generate_config inference ─────────────────────────────────────────────

    def test_generate_auto_deepseek_messages_bearer(self):
        config = generate_config(
            api_key="sk-test",
            base_url="https://api.deepseek.com/anthropic",
        )
        assert config.api_backend == "messages"
        assert config.auth_scheme == "bearer"

    def test_generate_auto_anthropic_messages_x_api_key(self):
        config = generate_config(
            api_key="sk-test",
            base_url="https://api.anthropic.com/v1",
        )
        assert config.api_backend == "messages"
        assert config.auth_scheme == "x_api_key"

    def test_generate_auto_openai_chat_completions(self):
        config = generate_config(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
        )
        assert config.api_backend == "chat_completions"
        assert config.auth_scheme == "bearer"

    def test_generate_explicit_backend_override(self):
        config = generate_config(
            api_key="sk-test",
            base_url="https://api.deepseek.com/anthropic",
            api_backend="responses",
        )
        assert config.api_backend == "responses"

    def test_generate_enum_backend_coerced_to_string(self):
        from code_agent.api.schemas import ApiBackend

        config = generate_config(
            api_key="sk-test",
            api_backend=ApiBackend.CHAT_COMPLETIONS,
        )
        assert config.api_backend == "chat_completions"

    def test_generate_enum_auth_scheme_coerced_to_string(self):
        from code_agent.api.schemas import AuthScheme

        config = generate_config(
            api_key="sk-test",
            auth_scheme=AuthScheme.X_API_KEY,
        )
        assert config.auth_scheme == "x_api_key"

    def test_generate_defaults_when_omitted(self):
        config = generate_config(api_key="sk-defaults")
        assert config.api_key == "sk-defaults"
        assert config.base_url == "https://api.deepseek.com/anthropic"
        assert config.model == "deepseek-v4-pro"
        assert config.max_tokens == 4096
        assert config.temperature == 0.7

    # ─── load_config edge cases ────────────────────────────────────────────────

    def test_load_config_missing_file(self, tmp_path: Path):
        path = tmp_path / "no" / "such" / "config.yaml"
        assert load_config(path) is None

    def test_load_config_empty_file(self, tmp_path: Path):
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        assert load_config(path) is None

    def test_load_config_whitespace_file(self, tmp_path: Path):
        path = tmp_path / "blank.yaml"
        path.write_text("   \n\n  \n", encoding="utf-8")
        assert load_config(path) is None

    def test_load_config_malformed_yaml(self, tmp_path: Path):
        # Malformed YAML must degrade to None, not crash the caller.
        path = tmp_path / "broken.yaml"
        path.write_text("provider: {unclosed", encoding="utf-8")
        assert load_config(path) is None

    def test_load_config_scalar_top_level(self, tmp_path: Path):
        path = tmp_path / "scalar.yaml"
        path.write_text("just a plain string", encoding="utf-8")
        assert load_config(path) is None

    def test_load_config_provider_not_a_mapping(self, tmp_path: Path):
        path = tmp_path / "providerlist.yaml"
        path.write_text("provider:\n  - a\n  - b\n", encoding="utf-8")
        assert load_config(path) is None

    def test_load_config_missing_provider_block(self, tmp_path: Path):
        path = tmp_path / "noprovider.yaml"
        path.write_text("other: value\n", encoding="utf-8")
        assert load_config(path) is None

    def test_load_config_empty_api_key(self, tmp_path: Path):
        path = tmp_path / "nokey.yaml"
        path.write_text("provider:\n  api_key: \"\"\n  base_url: https://x.test.com\n",
                        encoding="utf-8")
        assert load_config(path) is None

    # ─── is_present / EvilCodeConfig behavior ─────────────────────────────────

    def test_is_present_true_with_key(self):
        assert EvilCodeConfig(api_key="sk-1").is_present()

    def test_is_present_false_without_key(self):
        assert not EvilCodeConfig().is_present()

    def test_is_present_false_with_empty_key(self):
        assert not EvilCodeConfig(api_key="").is_present()

    def test_is_present_ignores_other_fields(self):
        config = EvilCodeConfig(
            base_url="https://x.test.com",
            model="some-model",
            api_backend="chat_completions",
        )
        assert not config.is_present()
