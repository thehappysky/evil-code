"""Tests for discrete cli/main.py functions.

Architecture seam guidance (from the mapping): the high-risk / hard-to-mock
entry points (``main``, ``_async_main``, ``_run_tui``, ``_run_interactive``,
``_resolve_session``, ``_resolve_oauth_config``, ``_run_headless``) are NOT
tested here. This module covers only the pure/self-contained seams:

1. ``config_from_profile``   — managed-provider-profile -> ProviderConfig
2. ``infer_backend`` /
   ``infer_auth_scheme``     — argparse-Namespace wrappers around the
                                already-tested *_from_url cores
3. ``_run_config_command``    — config set/show/clear end-to-end against tmp_path
4. ``_build_tools``           — built-in tool registry assembly
5. ``list_sessions``         — session listing for a cwd
6. ``_close_phase2``          — resource cleanup after any execution mode
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from pydantic import BaseModel

from code_agent.api.schemas import ApiBackend, AuthScheme, ProviderConfig
from code_agent.cli.auth import (
    config_from_profile,
    infer_auth_scheme,
    infer_auth_scheme_from_url,
    infer_backend,
    infer_backend_from_url,
)
from code_agent.cli.execution import list_sessions
from code_agent.cli.assembly import (
    build_tools,
    close_phase2,
)
from code_agent.cli.subcommands.config import run_config_command as _run_config_command
from code_agent.storage.models import SessionMeta
from code_agent.storage.session_store import SessionStore


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_profile(
    protocol: str = "openai-chat",
    base_url: str = "https://api.openai.com/v1",
    api_key: str = "sk-profile-key",
    model: str = "gpt-4o",
) -> SimpleNamespace:
    """Build a lightweight stand-in for a managed provider profile."""
    return SimpleNamespace(
        protocol=protocol, base_url=base_url, api_key=api_key, model=model,
    )


# ─── config_from_profile ────────────────────────────────────────────────────


class TestConfigFromProfile:
    """Map a managed provider profile into a ProviderConfig."""

    def test_openai_chat_maps_to_chat_completions(self):
        """openai-chat protocol -> CHAT_COMPLETIONS backend + Bearer auth."""
        cfg = config_from_profile(_make_profile(protocol="openai-chat"))
        assert isinstance(cfg, ProviderConfig)
        assert cfg.api_backend == ApiBackend.CHAT_COMPLETIONS
        assert cfg.auth_scheme == AuthScheme.BEARER

    def test_openai_responses_maps_to_responses(self):
        """openai-responses protocol -> RESPONSES backend."""
        cfg = config_from_profile(_make_profile(protocol="openai-responses"))
        assert cfg.api_backend == ApiBackend.RESPONSES

    def test_anthropic_messages_maps_to_messages(self):
        """anthropic-messages protocol -> MESSAGES backend."""
        cfg = config_from_profile(_make_profile(protocol="anthropic-messages"))
        assert cfg.api_backend == ApiBackend.MESSAGES

    def test_gemini_protocol_maps_to_chat_completions(self):
        """gemini-generate-content protocol -> CHAT_COMPLETIONS backend."""
        cfg = config_from_profile(
            _make_profile(protocol="gemini-generate-content"),
        )
        assert cfg.api_backend == ApiBackend.CHAT_COMPLETIONS

    def test_unknown_protocol_defaults_to_chat_completions(self):
        """An unrecognized protocol falls back to CHAT_COMPLETIONS."""
        cfg = config_from_profile(_make_profile(protocol="weird-vendor"))
        assert cfg.api_backend == ApiBackend.CHAT_COMPLETIONS

    def test_anthropic_base_url_yields_x_api_key(self):
        """anthropic-messages + api.anthropic.com base_url -> X_API_KEY."""
        cfg = config_from_profile(
            _make_profile(
                protocol="anthropic-messages",
                base_url="https://api.anthropic.com/v1",
            ),
        )
        assert cfg.api_backend == ApiBackend.MESSAGES
        assert cfg.auth_scheme == AuthScheme.X_API_KEY

    def test_messages_without_anthropic_host_yields_bearer(self):
        """anthropic-messages against a non-anthropic host -> Bearer."""
        cfg = config_from_profile(
            _make_profile(
                protocol="anthropic-messages",
                base_url="https://api.deepseek.com/anthropic",
            ),
        )
        assert cfg.api_backend == ApiBackend.MESSAGES
        assert cfg.auth_scheme == AuthScheme.BEARER

    def test_non_messages_backend_always_bearer(self):
        """CHAT_COMPLETIONS backend is Bearer even for an anthropic host."""
        cfg = config_from_profile(
            _make_profile(
                protocol="openai-chat",
                base_url="https://api.anthropic.com/v1",
            ),
        )
        assert cfg.api_backend == ApiBackend.CHAT_COMPLETIONS
        assert cfg.auth_scheme == AuthScheme.BEARER

    def test_credential_fields_are_copied(self):
        """base_url / api_key / model are copied verbatim from the profile."""
        cfg = config_from_profile(
            _make_profile(
                protocol="anthropic-messages",
                base_url="https://api.anthropic.com/v1",
                api_key="sk-abc-123",
                model="claude-sonnet-4",
            ),
        )
        assert cfg.base_url == "https://api.anthropic.com/v1"
        assert cfg.api_key == "sk-abc-123"
        assert cfg.model == "claude-sonnet-4"


# ─── infer_backend / infer_auth_scheme (argparse wrappers) ─────────────────


class TestInferBackend:
    """The Namespace wrapper delegates to infer_backend_from_url."""

    def test_wrapper_matches_core_for_anthropic(self):
        args = argparse.Namespace(base_url="https://api.anthropic.com/v1")
        expected = infer_backend_from_url("https://api.anthropic.com/v1")
        assert infer_backend(args) == expected
        assert infer_backend(args) == ApiBackend.MESSAGES

    def test_wrapper_matches_core_for_openai(self):
        args = argparse.Namespace(base_url="https://api.openai.com/v1")
        expected = infer_backend_from_url("https://api.openai.com/v1")
        assert infer_backend(args) == expected
        assert infer_backend(args) == ApiBackend.CHAT_COMPLETIONS

    def test_wrapper_matches_core_for_deepseek(self):
        args = argparse.Namespace(base_url="https://api.deepseek.com/anthropic")
        expected = infer_backend_from_url("https://api.deepseek.com/anthropic")
        assert infer_backend(args) == expected
        assert infer_backend(args) == ApiBackend.MESSAGES

    def test_provider_flag_overrides_url(self):
        """provider='messages' wins over an OpenAI-looking URL."""
        args = argparse.Namespace(
            base_url="https://api.openai.com/v1", provider="messages",
        )
        assert infer_backend(args) == ApiBackend.MESSAGES

    def test_provider_chat_completions_override(self):
        args = argparse.Namespace(
            base_url="https://api.anthropic.com/v1",
            provider="chat_completions",
        )
        assert infer_backend(args) == ApiBackend.CHAT_COMPLETIONS

    def test_missing_provider_attr_defaults_to_auto(self):
        """getattr(args, 'provider', 'auto') keeps Namespaces without provider working."""
        args = argparse.Namespace(base_url="https://api.openai.com/v1")
        assert infer_backend(args) == ApiBackend.CHAT_COMPLETIONS

    def test_none_base_url_treated_as_empty(self):
        """args.base_url or '' — a None base_url must not crash."""
        args = argparse.Namespace(base_url=None)
        assert infer_backend(args) == infer_backend_from_url("")


class TestInferAuthScheme:
    """The Namespace wrapper delegates to infer_auth_scheme_from_url."""

    def test_wrapper_matches_core_for_anthropic(self):
        args = argparse.Namespace(base_url="https://api.anthropic.com/v1")
        backend = infer_backend(args)
        expected = infer_auth_scheme_from_url(
            "https://api.anthropic.com/v1", backend,
        )
        assert infer_auth_scheme(args) == expected
        assert infer_auth_scheme(args) == AuthScheme.X_API_KEY

    def test_wrapper_matches_core_for_deepseek(self):
        args = argparse.Namespace(base_url="https://api.deepseek.com/anthropic")
        backend = infer_backend(args)
        expected = infer_auth_scheme_from_url(
            "https://api.deepseek.com/anthropic", backend,
        )
        assert infer_auth_scheme(args) == expected
        assert infer_auth_scheme(args) == AuthScheme.BEARER

    def test_wrapper_bearer_for_openai(self):
        args = argparse.Namespace(base_url="https://api.openai.com/v1")
        assert infer_auth_scheme(args) == AuthScheme.BEARER

    def test_none_base_url_treated_as_empty(self):
        args = argparse.Namespace(base_url=None)
        assert infer_auth_scheme(args) == AuthScheme.BEARER


# ─── _run_config_command ─────────────────────────────────────────────────────

_CONFIG_KEY = "sk-test-1234567890abcdef"  # long enough to be meaningfully masked
_CONFIG_BASE = "https://api.deepseek.com/anthropic"
_CONFIG_MODEL = "deepseek-v4-pro"


@pytest.mark.use_real_config
class TestRunConfigCommand:
    """config set/show/clear end-to-end against tmp_path.

    The ``set`` subcommand writes ``~/.evil-code/config.yaml`` through
    ``config_store._CONFIG_FILE`` and ``~/.claude/settings.json`` through
    ``settings_writer._DEFAULT_SETTINGS_PATH``. The ``clear`` subcommand,
    however, removes ``Path.home()/.evil-code/config.yaml`` directly
    (it does not read ``_CONFIG_FILE``). To keep the round-trip consistent
    AND never touch the real home directory we therefore redirect
    ``_CONFIG_FILE`` under ``tmp_path/.evil-code/`` and additionally patch
    ``Path.home`` to return ``tmp_path``, so ``set`` writes and ``clear``
    deletes the same (temporary) file.
    """

    @pytest.fixture(autouse=True)
    def _isolate_config_files(self, tmp_path: Path, monkeypatch):
        """Point every config file constant at tmp_path."""
        monkeypatch.setattr(
            "code_agent.auth.config_store._CONFIG_DIR",
            tmp_path / ".evil-code",
        )
        monkeypatch.setattr(
            "code_agent.auth.config_store._CONFIG_FILE",
            tmp_path / ".evil-code" / "config.yaml",
        )
        monkeypatch.setattr(
            "code_agent.config.settings_writer._DEFAULT_SETTINGS_PATH",
            tmp_path / "settings.json",
        )
        # clear hardcodes Path.home()/.evil-code/config.yaml.
        monkeypatch.setattr(
            Path, "home", classmethod(lambda cls: tmp_path),
        )

    def test_set_cli_args_writes_both_files(self, tmp_path: Path):
        """--api-key/--base-url/--model mode writes config.yaml + settings.json."""
        _run_config_command(
            [
                "set",
                "--api-key", _CONFIG_KEY,
                "--base-url", _CONFIG_BASE,
                "--model", _CONFIG_MODEL,
            ],
        )

        # Primary: evil-code config.yaml
        cfg_path = tmp_path / ".evil-code" / "config.yaml"
        assert cfg_path.exists()
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        assert data["provider"]["api_key"] == _CONFIG_KEY
        assert data["provider"]["base_url"] == _CONFIG_BASE
        assert data["provider"]["model"] == _CONFIG_MODEL

        # Fallback: settings.json env block (Bearer -> ANTHROPIC_AUTH_TOKEN)
        settings = json.loads(
            (tmp_path / "settings.json").read_text(encoding="utf-8"),
        )
        assert settings["env"]["ANTHROPIC_AUTH_TOKEN"] == _CONFIG_KEY
        assert settings["env"]["ANTHROPIC_BASE_URL"] == _CONFIG_BASE
        assert settings["env"]["ANTHROPIC_MODEL"] == _CONFIG_MODEL
        assert "ANTHROPIC_API_KEY" not in settings["env"]

    def test_set_x_api_key_writes_api_key_var(self, tmp_path: Path):
        """--auth-scheme x_api_key -> ANTHROPIC_API_KEY instead of AUTH_TOKEN."""
        _run_config_command(
            [
                "set",
                "--api-key", _CONFIG_KEY,
                "--auth-scheme", "x_api_key",
                "--base-url", _CONFIG_BASE,
                "--model", _CONFIG_MODEL,
            ],
        )
        settings = json.loads(
            (tmp_path / "settings.json").read_text(encoding="utf-8"),
        )
        assert settings["env"]["ANTHROPIC_API_KEY"] == _CONFIG_KEY
        assert "ANTHROPIC_AUTH_TOKEN" not in settings["env"]

        cfg = yaml.safe_load(
            (tmp_path / ".evil-code" / "config.yaml").read_text(encoding="utf-8"),
        )
        assert cfg["provider"]["auth_scheme"] == "x_api_key"

    def test_set_interactive_mode_prompts_for_missing_args(
        self, tmp_path: Path, monkeypatch,
    ):
        """With no CLI args, input() supplies key and defaults fill the rest."""
        responses = iter(["sk-interactive-key", "", ""])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
        _run_config_command(["set"])

        data = yaml.safe_load(
            (tmp_path / ".evil-code" / "config.yaml").read_text(encoding="utf-8"),
        )
        assert data["provider"]["api_key"] == "sk-interactive-key"
        assert data["provider"]["base_url"] == "https://api.deepseek.com/anthropic"
        assert data["provider"]["model"] == "deepseek-v4-pro"

    def test_set_empty_api_key_exits(self, monkeypatch):
        """An empty API key aborts with exit code 1."""
        monkeypatch.setattr("builtins.input", lambda prompt="": "")
        with pytest.raises(SystemExit) as excinfo:
            _run_config_command(["set"])
        assert excinfo.value.code == 1

    def test_show_masks_api_key(self, tmp_path: Path, capsys):
        """show prints a masked key and never the raw secret."""
        _run_config_command(
            [
                "set",
                "--api-key", _CONFIG_KEY,
                "--base-url", _CONFIG_BASE,
                "--model", _CONFIG_MODEL,
            ],
        )
        _run_config_command(["show"])
        captured = capsys.readouterr()

        assert _CONFIG_KEY not in captured.out
        assert _CONFIG_KEY[:8] in captured.out  # masked prefix is shown
        assert "..." in captured.out
        assert "[configured]" in captured.out

    def test_show_before_any_set_reports_not_configured(self, capsys):
        """show with no files present -> [not configured] and no crash."""
        _run_config_command(["show"])
        captured = capsys.readouterr()
        assert "[not configured]" in captured.out

    def test_clear_removes_config_and_preserves_settings_keys(
        self, tmp_path: Path, capsys,
    ):
        """clear deletes config.yaml and drops auth env keys, keeping others."""
        # Seed settings.json with unrelated keys to prove preservation.
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(
            json.dumps({
                "model": "claude-opus",
                "env": {"ANTHROPIC_AUTH_TOKEN": "sk-stale", "FOO": "bar"},
            }),
            encoding="utf-8",
        )

        _run_config_command(
            [
                "set",
                "--api-key", _CONFIG_KEY,
                "--base-url", _CONFIG_BASE,
                "--model", _CONFIG_MODEL,
            ],
        )

        cfg_path = tmp_path / ".evil-code" / "config.yaml"
        assert cfg_path.exists()

        # set must merge, not clobber, the pre-existing keys.
        before = json.loads(settings_path.read_text(encoding="utf-8"))
        assert before["model"] == "claude-opus"
        assert before["env"]["FOO"] == "bar"
        assert before["env"]["ANTHROPIC_AUTH_TOKEN"] == _CONFIG_KEY

        _run_config_command(["clear"])
        captured = capsys.readouterr()
        assert "Cleared auth config" in captured.out

        # Primary config file is gone.
        assert not cfg_path.exists()
        # Auth env keys cleared, unrelated keys preserved.
        after = json.loads(settings_path.read_text(encoding="utf-8"))
        assert after["model"] == "claude-opus"
        assert after["env"]["FOO"] == "bar"
        assert "ANTHROPIC_AUTH_TOKEN" not in after["env"]
        assert "ANTHROPIC_BASE_URL" not in after["env"]
        assert "ANTHROPIC_MODEL" not in after["env"]
        assert "ANTHROPIC_API_KEY" not in after["env"]

    def test_unknown_subcommand_exits(self, capsys):
        """A bad subcommand prints an error and exits 1."""
        with pytest.raises(SystemExit) as excinfo:
            _run_config_command(["frobnicate"])
        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "Unknown config subcommand: frobnicate" in captured.out

    def test_help_prints_usage_without_exit(self, capsys):
        """No args (or -h) prints usage and returns normally."""
        _run_config_command([])
        captured = capsys.readouterr()
        assert "Usage:" in captured.out
        assert "config set" in captured.out


# ─── _build_tools ────────────────────────────────────────────────────────────


class TestBuildTools:
    """The built-in tool registry assembly."""

    def test_registers_seven_builtin_tools(self):
        """All built-ins are registered by name (incl. file_glob, file_delete)."""
        registry = build_tools()
        assert set(registry.list_names()) == {
            "file_read",
            "file_write",
            "file_edit",
            "file_delete",
            "file_search",
            "file_glob",
            "shell_exec",
            "web_fetch",
        }

    def test_tool_categories(self):
        """Each built-in carries its expected category tag."""
        registry = build_tools()
        cats = {name: registry._tools[name].category for name in registry.list_names()}
        assert cats["file_read"] == "filesystem"
        assert cats["file_write"] == "filesystem"
        assert cats["file_edit"] == "filesystem"
        assert cats["file_delete"] == "filesystem"
        assert cats["file_search"] == "filesystem"
        assert cats["file_glob"] == "filesystem"
        assert cats["shell_exec"] == "system"
        assert cats["web_fetch"] == "web"

    def test_registry_is_finalized(self):
        """Registering after finalize() is rejected."""
        registry = build_tools()
        assert registry._finalized is True
        with pytest.raises(RuntimeError):

            class LateInput(BaseModel):
                pass

            registry.register("late_tool", LateInput, description="too late")(
                lambda: None,
            )

    def test_definitions_are_exportable(self):
        """get_definitions() yields OpenAI-format definitions for all tools."""
        registry = build_tools()
        defs = registry.get_definitions()
        assert len(defs) == 8
        assert all(d["type"] == "function" for d in defs)
        names = {d["function"]["name"] for d in defs}
        assert names == set(registry.list_names())

    def test_skill_tool_added_when_skill_manager_provided(self):
        """Passing a skill_manager adds the 'skill' tool."""
        registry = build_tools(skill_manager=object())
        names = registry.list_names()
        assert "skill" in names
        assert len(names) == 9
        assert registry._tools["skill"].category == "skill"

    def test_web_search_added_only_for_responses_backend(self):
        """web_search is registered only for Responses-capable providers."""
        from code_agent.api.schemas import ApiBackend, ProviderConfig

        # Non-responses provider → no web_search.
        cfg = ProviderConfig(api_backend=ApiBackend.CHAT_COMPLETIONS)
        assert "web_search" not in build_tools(web_search_config=cfg).list_names()

        # Responses provider → web_search present.
        cfg_r = ProviderConfig(api_backend=ApiBackend.RESPONSES, api_key="sk-x")
        names = build_tools(web_search_config=cfg_r).list_names()
        assert "web_search" in names

    def test_capability_mode_filters_tools(self):
        """Sub-agent capability modes restrict the toolset."""
        read_only = build_tools(capability_mode="read-only")
        assert set(read_only.list_names()) == {
            "file_read", "file_search", "file_glob", "web_fetch",
        }

        execute = build_tools(capability_mode="execute")
        assert "shell_exec" in execute.list_names()
        assert "file_edit" in execute.list_names()

    def test_task_tool_registered_with_factory(self):
        """Passing a subagent_factory registers the task tool."""
        async def _spawn(prompt, subagent_type, capability_mode):
            return "ok"

        reg = build_tools(subagent_factory=_spawn)
        assert "task" in reg.list_names()
        assert reg._tools["task"].category == "subagent"

    async def test_execute_roundtrip_via_file_tools(self, tmp_path: Path):
        """The finalized registry executes the built-in file_write/file_read."""
        registry = build_tools()
        target = tmp_path / "hello.txt"

        write_result = await registry.execute(
            "file_write",
            {"file_path": str(target), "content": "hi from tool"},
        )
        assert not write_result.is_error
        assert target.read_text(encoding="utf-8") == "hi from tool"

        read_result = await registry.execute(
            "file_read", {"file_path": str(target)},
        )
        assert not read_result.is_error
        assert "hi from tool" in read_result.output

    # [2026-08-04] Phase 4 wiring: plugin tools + sandbox shell

    async def test_plugin_tools_registered(self):
        """plugin_tools are registered as '{plugin}__{fn}' with permissive args."""
        async def demo_search(query: str):
            return f"found: {query}"

        registry = build_tools(plugin_tools=[("demo", demo_search)])
        name = "demo__demo_search"
        assert name in registry.list_names()
        assert registry._tools[name].category == "plugin"

        result = await registry.execute(name, {"query": "hello"})
        assert not result.is_error
        assert result.output == "found: hello"

    def test_plugin_tools_can_carry_strict_input_model(self):
        """A plugin may attach __input_model__ for a strict schema."""
        from pydantic import BaseModel, Field

        class StrictArgs(BaseModel):
            query: str = Field(description="query")

        async def demo_search(query: str):
            return query

        demo_search.__input_model__ = StrictArgs  # type: ignore[attr-defined]
        registry = build_tools(plugin_tools=[("demo", demo_search)])
        assert registry._tools["demo__demo_search"].input_model is StrictArgs

    def test_sandbox_shell_exec_wrapper(self):
        """With sandbox=True, shell_exec routes through the sandbox manager."""
        registry = build_tools(sandbox=True)
        assert "shell_exec" in registry.list_names()
        handler = registry._tools["shell_exec"].handler
        assert handler.__name__ == "shell_exec_routed"

    def test_finalize_false_allows_late_registration(self):
        """finalize=False lets callers register tools (e.g. MCP) before locking."""
        from pydantic import BaseModel

        registry = build_tools(finalize=False)
        assert registry._finalized is False

        class LateInput(BaseModel):
            pass

        async def late_tool(input: LateInput):
            return "ok"

        registry.register("late_tool", LateInput, description="late")(
            late_tool,
        )
        registry.finalize()
        assert registry._finalized is True
        assert "late_tool" in registry.list_names()


# ─── list_sessions ──────────────────────────────────────────────────────────


def _short_abs_cwd(suffix: str) -> str:
    """A short absolute cwd for session-store tests.

    SessionStore encodes the cwd into the on-disk directory name, and on
    Windows the full tmp_path is long enough to blow MAX_PATH once base64
    encoded (WinError 206). Using a short absolute path keeps the encoded
    directory short. The directory itself never needs to exist — only the
    string matters for ``list_sessions``'s ``s.cwd == os.path.abspath(cwd)``.
    """
    return os.path.abspath(suffix)


class TestListSessions:
    """Session listing filtered by the requested cwd."""

    def _list(self, cwd: str) -> None:
        list_sessions(argparse.Namespace(cwd=cwd))

    def test_lists_matching_session(self, tmp_path: Path, monkeypatch, capsys):
        """A session whose cwd matches args.cwd is printed by id."""
        monkeypatch.setattr("code_agent.cli.execution.get_data_dir", lambda: tmp_path)
        cwd = _short_abs_cwd("proj-alpha")

        store = SessionStore(root_dir=tmp_path / "sessions")
        asyncio.run(store.init_session(SessionMeta(
            session_id="s_20260802_abcd1234",
            cwd=cwd,
            model_id="deepseek-v4-pro",
            title="My Session",
            prompt_count=5,
            total_tokens=1200,
        )))

        self._list(cwd)
        captured = capsys.readouterr()
        assert "Saved sessions for" in captured.out
        assert "s_20260802_abcd1234" in captured.out
        assert "My Session" in captured.out
        assert "Total: 1 session(s)" in captured.out

    def test_filters_by_cwd(self, tmp_path: Path, monkeypatch, capsys):
        """Sessions from other directories are excluded."""
        monkeypatch.setattr("code_agent.cli.execution.get_data_dir", lambda: tmp_path)
        cwd = _short_abs_cwd("proj-alpha")
        other_cwd = _short_abs_cwd("proj-beta")

        store = SessionStore(root_dir=tmp_path / "sessions")
        asyncio.run(store.init_session(SessionMeta(
            session_id="s_20260802_zzzz9999",
            cwd=cwd,
            model_id="deepseek-v4-pro",
        )))
        asyncio.run(store.init_session(SessionMeta(
            session_id="s_20260802_yyyy8888",
            cwd=other_cwd,
            model_id="deepseek-v4-pro",
        )))

        self._list(other_cwd)
        captured = capsys.readouterr()
        assert "s_20260802_yyyy8888" in captured.out
        assert "s_20260802_zzzz9999" not in captured.out

    def test_empty_store_prints_no_saved_sessions(
        self, tmp_path: Path, monkeypatch, capsys,
    ):
        """No sessions for the cwd -> the 'No saved sessions' message."""
        monkeypatch.setattr("code_agent.cli.execution.get_data_dir", lambda: tmp_path)
        self._list(os.path.abspath(str(tmp_path)))
        captured = capsys.readouterr()
        assert "No saved sessions for current directory." in captured.out

    def test_errors_are_caught(self, tmp_path: Path, monkeypatch, capsys):
        """A storage failure degrades to a stderr message, not a crash."""
        monkeypatch.setattr("code_agent.cli.execution.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr(
            SessionStore, "list_sessions",
            MagicMock(side_effect=RuntimeError("disk exploded")),
        )
        self._list(os.path.abspath(str(tmp_path)))
        captured = capsys.readouterr()
        assert "Failed to list sessions" in captured.err
        assert "disk exploded" in captured.err


# ─── _close_phase2 ───────────────────────────────────────────────────────────


class TestClosePhase2:
    """Resource cleanup after any execution mode."""

    async def test_closes_memory_index_and_trace(self, capsys):
        """Both the memory index and the trace are closed; trace path printed."""
        memory_index = AsyncMock()
        trace = MagicMock()
        trace.session_id = "trace-abc123"

        await close_phase2(memory_index, trace)

        memory_index.close.assert_awaited_once()
        trace.close.assert_called_once()
        captured = capsys.readouterr()
        assert "trace-abc123.jsonl" in captured.err

    async def test_memory_index_close_error_is_swallowed(self):
        """A failing memory_index.close() must not propagate."""
        memory_index = AsyncMock()
        memory_index.close.side_effect = RuntimeError("db busy")

        await close_phase2(memory_index, None)

        memory_index.close.assert_awaited_once()

    async def test_none_memory_index_is_safe(self):
        """memory_index=None is tolerated."""
        await close_phase2(None, None)

    async def test_no_trace_means_no_trace_output(self, capsys):
        """Without a trace, nothing is printed to stderr."""
        await close_phase2(None, None)
        captured = capsys.readouterr()
        assert "[Trace]" not in captured.out + captured.err
