"""Exercise real entry-point assembly without remote services."""

import importlib
import json
from unittest.mock import AsyncMock

from code_agent.api.schemas import ProviderConfig
from code_agent.cli.args import build_parser
from code_agent.core.session import Session
from code_agent.permission.manager import PermissionManager
from code_agent.tools.result import ToolResult
from conftest import MockChatClient, make_text_response, make_tool_call_response


async def test_headless_routes_auto_approved_command_to_shared_sandbox(monkeypatch, tmp_path):
    main = importlib.import_module("code_agent.cli.main")
    assembly = importlib.import_module("code_agent.cli.assembly")
    sandbox = importlib.import_module("code_agent.sandbox.manager")
    calls = []

    class FakeSandbox:
        mode = "auto"
        is_available = AsyncMock(return_value=True)
        close = AsyncMock()
        gc = AsyncMock()

        def __init__(self, **kwargs):
            pass

        async def run(self, command, **kwargs):
            assert kwargs["allow_fallback"] is False
            calls.append(command)
            return ToolResult.ok("sandbox result")

    args = build_parser().parse_args(["check", "--cwd", str(tmp_path)])
    client = MockChatClient()
    client.close = AsyncMock()
    client.set_responses([
        make_tool_call_response("c1", "shell_exec", json.dumps({"command": "python -c pass"})),
        make_text_response("done"),
    ])
    pm = PermissionManager()
    monkeypatch.delenv("CODE_AGENT_SANDBOX", raising=False)
    monkeypatch.setattr(main, "config", ProviderConfig(api_key="test"), raising=False)
    monkeypatch.setattr(main, "trace", None, raising=False)
    monkeypatch.setattr(main, "build_resilient_client", lambda *a: client)
    monkeypatch.setattr(assembly, "resolve_session", AsyncMock(return_value=(Session("Test"), {})))
    monkeypatch.setattr(assembly, "init_phase2_modules", lambda a: {"permission_mgr": pm})
    monkeypatch.setattr(assembly, "close_phase2", AsyncMock())
    monkeypatch.setattr(sandbox, "SandboxManager", FakeSandbox)

    await main._async_main(args)

    assert calls == ["python -c pass"]
    assert any(m.get("content") == "sandbox result" for m in client.call_history[1]["messages"])
    FakeSandbox.close.assert_awaited_once()
    client.close.assert_awaited_once()


async def test_tui_builds_real_runner_and_subagent_factory(monkeypatch, tmp_path):
    main = importlib.import_module("code_agent.cli.main")
    stores = importlib.import_module("code_agent.tui.stores")
    tui = importlib.import_module("code_agent.tui.tui")
    provider_store = stores.JsonProviderSettingsStore(tmp_path / "providers.json")
    ui_store = stores.JsonUiPreferencesStore(tmp_path / "ui.json")
    client = MockChatClient()
    client.close = AsyncMock()
    client.set_responses([make_text_response("TUI ready")])
    results = []

    class FakeApp:
        return_code = 0

        def __init__(self, controller, **kwargs):
            self.controller = controller

        async def run_async(self):
            results.append(await self.controller.run("hello"))

    monkeypatch.setattr(stores, "JsonProviderSettingsStore", lambda: provider_store)
    monkeypatch.setattr(stores, "JsonUiPreferencesStore", lambda: ui_store)
    monkeypatch.setattr(tui, "EvilCodeApp", FakeApp)
    monkeypatch.setattr(main, "build_resilient_client", lambda *a: client)
    monkeypatch.setattr(main, "trace", None, raising=False)
    args = build_parser().parse_args(["--tui", "--cwd", str(tmp_path)])

    assert await main._run_tui(args, Session("Test"), {"permission_mgr": PermissionManager()}, ProviderConfig(api_key="test")) == 0
    assert client.call_history
    assert results
    client.close.assert_awaited_once()


async def test_environment_off_overrides_sandbox_flag(monkeypatch, tmp_path):
    main = importlib.import_module("code_agent.cli.main")
    assembly = importlib.import_module("code_agent.cli.assembly")
    sandbox = importlib.import_module("code_agent.sandbox.manager")
    from code_agent.tools.builtin.shell_exec import shell_exec

    client = MockChatClient()
    client.set_responses([make_text_response("ready")])
    captured = []
    original_build_agent = assembly.build_agent

    def capture_agent(*args, **kwargs):
        agent = original_build_agent(*args, **kwargs)
        captured.append(agent)
        return agent

    def unexpected_sandbox(**kwargs):
        raise AssertionError("CODE_AGENT_SANDBOX=off must not create a sandbox")

    monkeypatch.setenv("CODE_AGENT_SANDBOX", "off")
    monkeypatch.setattr(main, "config", ProviderConfig(api_key="test"), raising=False)
    monkeypatch.setattr(main, "trace", None, raising=False)
    monkeypatch.setattr(main, "build_resilient_client", lambda *a: client)
    monkeypatch.setattr(assembly, "resolve_session", AsyncMock(return_value=(Session("Test"), {})))
    monkeypatch.setattr(assembly, "init_phase2_modules", lambda a: {"permission_mgr": PermissionManager()})
    monkeypatch.setattr(assembly, "close_phase2", AsyncMock())
    monkeypatch.setattr(assembly, "build_agent", capture_agent)
    monkeypatch.setattr(assembly, "SandboxManager", unexpected_sandbox)
    monkeypatch.setattr(sandbox, "SandboxManager", unexpected_sandbox)
    args = build_parser().parse_args(["check", "--sandbox", "--cwd", str(tmp_path)])

    await main._async_main(args)
    assert captured[0]._tools._tools["shell_exec"].handler is shell_exec
