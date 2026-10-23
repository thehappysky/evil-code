"""Runtime ownership must survive cancellation and independent close errors."""

import asyncio
from contextlib import AsyncExitStack
from unittest.mock import AsyncMock

import pytest

from code_agent.core.agent import Agent
from code_agent.core.session import Session
from code_agent.runtime import close_resource, open_runtime
from code_agent.tools.registry import ToolRegistry
from conftest import MockChatClient


@pytest.mark.parametrize("failure", ["prepare", "agent", "cancel"])
async def test_runtime_closes_after_partial_construction(failure):
    client = MockChatClient()
    client.close = AsyncMock()

    async def prepare(tools):
        if failure == "prepare":
            raise RuntimeError("discovery failed")
        if failure == "cancel":
            raise asyncio.CancelledError

    def make_agent(session, client, tools):
        raise RuntimeError("agent construction failed")

    error = asyncio.CancelledError if failure == "cancel" else RuntimeError
    with pytest.raises(error):
        async with open_runtime(
            Session("Test"), create_client=lambda: client,
            create_tools=ToolRegistry, prepare_tools=prepare, create_agent=make_agent,
        ):
            pytest.fail("an incomplete runtime must never be exposed")
    client.close.assert_awaited_once()


async def test_client_close_failure_preserves_original_error(caplog):
    client = MockChatClient()
    client.close = AsyncMock(side_effect=RuntimeError("close failed"))
    with pytest.raises(ValueError, match="original"):
        async with open_runtime(
            Session("Test"), create_client=lambda: client,
            create_tools=ToolRegistry, create_agent=Agent,
        ):
            raise ValueError("original")
    assert "cleanup" in caplog.text


async def test_one_cleanup_failure_does_not_skip_other_resources():
    first = AsyncMock()
    broken = AsyncMock()
    broken.close.side_effect = RuntimeError("broken close")
    async with AsyncExitStack() as resources:
        resources.push_async_callback(close_resource, first)
        resources.push_async_callback(close_resource, broken)
    first.close.assert_awaited_once()
    broken.close.assert_awaited_once()
