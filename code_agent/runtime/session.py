"""One client lifetime for CLI, TUI bindings, and delegated agents."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

from code_agent.api.client import ChatClient
from code_agent.core.agent import Agent
from code_agent.core.session import Session
from code_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentRuntime:
    session: Session
    client: ChatClient
    tools: ToolRegistry
    agent: Agent


async def close_resource(resource, *, method: str = "close") -> None:
    """Finish independent cleanup even if another resource fails to close."""
    try:
        await getattr(resource, method)()
    except Exception:
        logger.warning("%s.%s failed during cleanup", type(resource).__name__, method, exc_info=True)


async def stop_background_task(task: asyncio.Task) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


@asynccontextmanager
async def open_runtime(
    session: Session,
    *,
    create_client: Callable[[], ChatClient],
    create_tools: Callable[[], ToolRegistry],
    create_agent: Callable[[Session, ChatClient, ToolRegistry], Agent],
    prepare_tools: Callable[[ToolRegistry], Awaitable[None]] | None = None,
) -> AsyncIterator[AgentRuntime]:
    """Build in order; close the acquired client on every exit path.

    Construction, tool discovery, execution, and cancellation all have the
    same ownership boundary. Tools, hooks, sandbox policy and UI adapters
    remain supplied by their owning composition layer.
    """
    client = create_client()
    try:
        tools = create_tools()
        if prepare_tools is not None:
            await prepare_tools(tools)
        agent = create_agent(session, client, tools)
        yield AgentRuntime(session, client, tools, agent)
    finally:
        await close_resource(client)
