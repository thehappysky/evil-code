"""Fault-injection tests at the real entry point, independent of runtime API."""

import asyncio
import importlib
from unittest.mock import AsyncMock

import pytest

from code_agent.api.schemas import ProviderConfig
from code_agent.cli.args import build_parser
from code_agent.core.session import Session
from code_agent.permission.manager import PermissionManager
from conftest import MockChatClient, make_text_response


@pytest.mark.parametrize("failure", ["tools", "agent", "cancel", "mcp"])
async def test_failed_startup_releases_every_acquired_resource(failure, monkeypatch, tmp_path):
    main = importlib.import_module("code_agent.cli.main")
    assembly = importlib.import_module("code_agent.cli.assembly")
    execution = importlib.import_module("code_agent.cli.execution")
    client = MockChatClient()
    client.close = AsyncMock()
    client.set_responses([make_text_response("ready")])
    close_modules = AsyncMock()
    monkeypatch.setenv("CODE_AGENT_SANDBOX", "off")
    monkeypatch.setattr(main, "config", ProviderConfig(api_key="test"), raising=False)
    monkeypatch.setattr(main, "trace", None, raising=False)
    monkeypatch.setattr(main, "build_resilient_client", lambda *a: client)
    monkeypatch.setattr(assembly, "resolve_session", AsyncMock(return_value=(Session("Test"), {})))
    monkeypatch.setattr(assembly, "init_phase2_modules", lambda a: {"permission_mgr": PermissionManager()})
    monkeypatch.setattr(assembly, "close_phase2", close_modules)
    args = build_parser().parse_args(["check", "--cwd", str(tmp_path)])
    mcp_close = AsyncMock()

    def fail(*a, **kw):
        raise RuntimeError("injected assembly failure")

    if failure in {"tools", "agent"}:
        monkeypatch.setattr(assembly, "build_" + failure, fail)
    elif failure == "cancel":
        monkeypatch.setattr(execution, "run_headless", AsyncMock(side_effect=asyncio.CancelledError))
    else:
        class BrokenMcp:
            connect = AsyncMock(side_effect=RuntimeError("connection failed"))
            close = mcp_close

            def __init__(self, *a, **kw):
                pass

        monkeypatch.setattr("code_agent.mcp.client.McpClient", BrokenMcp)
        args.mcp_server = "broken=python server.py"

    if failure == "mcp":
        await main._async_main(args)
        mcp_close.assert_awaited_once()
    else:
        error = asyncio.CancelledError if failure == "cancel" else RuntimeError
        with pytest.raises(error):
            await main._async_main(args)
    client.close.assert_awaited_once()
    close_modules.assert_awaited_once()
