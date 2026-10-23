# [Phase 4] 2026-07-29: MCP client — JSON-RPC 2.0 protocol client
"""MCP JSON-RPC client.

References:
    - design-docs/Python-Agent-MVP-Phase4-详细设计.md §3 mcp/client.py
    - Rust McpClient (xai-grok-mcp/src/servers.rs)

Protocol flow:
    1. initialize → exchange capabilities and server information
    2. tools/list → discover available tools (with cursor pagination)
    3. tools/call → invoke a specific tool by name
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field

from code_agent.mcp.transport import HttpTransport, StdioTransport

logger = logging.getLogger(__name__)


@dataclass
class McpServerConfig:
    """Configuration for an MCP server connection.

    Attributes:
        name: Server identifier (e.g., "github", "postgres").
        command: Command list for stdio transport (e.g., ["python", "server.py"]).
        url: URL for HTTP transport (e.g., "http://localhost:8080").
    """

    name: str
    command: list[str] | None = None
    url: str | None = None
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None


# ── McpClient ────────────────────────────────────────────────────────────────


class McpClient:
    """MCP JSON-RPC client.

    Supports two transport modes:
    - stdio: spawns child process, communicates via stdin/stdout
    - HTTP: connects to remote MCP server over HTTP + SSE

    Usage::

        config = McpServerConfig(
            name="my-server",
            command=["python", "mcp_server.py"],
        )
        client = McpClient(config)
        await client.connect()
        tools = await client.list_tools()
        result = await client.call_tool("search", {"query": "..."})
        await client.close()
    """

    def __init__(self, config: McpServerConfig):
        self._config = config
        self._transport: StdioTransport | HttpTransport | None = None
        self._request_id: int = 0
        self._connected = False
        self._server_info: dict = {}

    # ── Connection ────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Establish transport connection and perform MCP handshake.

        Raises:
            RuntimeError: If connection or handshake fails.
        """
        # Create transport
        if self._config.command:
            self._transport = StdioTransport(
                command=self._config.command,
                env=self._config.env,
            )
        elif self._config.url:
            self._transport = HttpTransport(
                url=self._config.url,
                headers=self._config.headers,
            )
            await self._transport.connect()
        else:
            raise ValueError(
                "McpServerConfig must have either 'command' or 'url'"
            )

        # Start stdio transport
        if isinstance(self._transport, object):
            if isinstance(self._transport, StdioTransport):
                await self._transport.start()

        # Handshake: initialize
        response = await self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "evil-code",
                "version": "0.2.0",
            },
        })

        if "error" in response:
            raise RuntimeError(
                f"MCP initialize failed: {response['error']}"
            )

        self._server_info = response.get("result", {})
        self._connected = True
        logger.info(
            "MCP client connected to '%s': %s",
            self._config.name,
            self._server_info.get("serverInfo", {}).get("name", "unknown"),
        )

    # ── Tool discovery ────────────────────────────────────────────────────

    async def list_tools(self) -> list[dict]:
        """List all tools provided by this MCP server.

        Handles cursor-based pagination for servers with many tools (>100).

        Returns:
            List of tool definition dicts.
        """
        if not self._connected:
            raise RuntimeError("MCP client not connected")

        all_tools: list[dict] = []
        cursor: str | None = None

        while True:
            params: dict = {}
            if cursor:
                params["cursor"] = cursor

            response = await self._request("tools/list", params)

            if "error" in response:
                raise RuntimeError(
                    f"MCP tools/list failed: {response['error']}"
                )

            result = response.get("result", {})
            tools = result.get("tools", [])
            all_tools.extend(tools)

            next_cursor = result.get("nextCursor")
            if not next_cursor or len(tools) == 0:
                break
            cursor = next_cursor

        logger.info(
            "MCP '%s': discovered %d tools",
            self._config.name, len(all_tools),
        )
        return all_tools

    # ── Tool invocation ───────────────────────────────────────────────────

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """Invoke a tool by name.

        Args:
            name: Tool name.
            arguments: Tool arguments dict.

        Returns:
            Raw JSON-RPC result dict.

        Raises:
            RuntimeError: If the server returns an error.
        """
        if not self._connected:
            raise RuntimeError("MCP client not connected")

        response = await self._request("tools/call", {
            "name": name,
            "arguments": arguments,
        })

        if "error" in response:
            error_msg = response["error"].get("message", "unknown error")
            raise RuntimeError(
                f"MCP tool '{name}' failed on '{self._config.name}': {error_msg}"
            )

        return response.get("result", {})

    # ── Cleanup ───────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Graceful shutdown: close transport, terminate child process."""
        if self._transport:
            await self._transport.close()
            self._transport = None
        self._connected = False
        logger.info("MCP client '%s' closed", self._config.name)

    @property
    def is_connected(self) -> bool:
        """True if transport is established and handshake completed."""
        return self._connected

    @property
    def server_info(self) -> dict:
        """Server information from initialize handshake."""
        return dict(self._server_info)

    @property
    def server_name(self) -> str:
        """Configured server name."""
        return self._config.name

    # ── Internal ──────────────────────────────────────────────────────────

    async def _request(self, method: str, params: dict | None = None) -> dict:
        """Send a JSON-RPC request and return the response.

        Args:
            method: JSON-RPC method name.
            params: Optional parameters dict.

        Returns:
            JSON-RPC response dict (may contain "error" key).
        """
        self._request_id += 1
        message = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }

        await self._transport.send(message)
        return await self._transport.receive()
