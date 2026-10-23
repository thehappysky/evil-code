# [Phase 4] 2026-07-29: MCP transport layer — stdio + HTTP transports for JSON-RPC
"""MCP transport layer.

References:
    - design-docs/Python-Agent-MVP-Phase4-详细设计.md §3 mcp/transport.py
    - MCP JSON-RPC 2.0 specification

Two transport modes:
    - StdioTransport: subprocess stdin/stdout JSON-RPC
    - HttpTransport: HTTP + SSE for remote MCP servers
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class StdioTransport:
    """Subprocess stdin/stdout JSON-RPC transport.

    Spawns MCP server as child process, communicates via line-delimited JSON.
    Each message is one line of JSON on stdout. Requests are sent via stdin.

    Usage::

        transport = StdioTransport(command=["python", "mcp_server.py"])
        await transport.start()
        await transport.send({"jsonrpc": "2.0", "method": "tools/list", ...})
        response = await transport.receive()
        await transport.close()
    """

    def __init__(
        self,
        command: list[str],
        env: dict[str, str] | None = None,
    ):
        self._command = command
        self._env = env or os.environ.copy()
        self._process: subprocess.Popen | None = None
        self._started = False
        self._closed = False

    async def start(self) -> None:
        """Start the MCP server subprocess.

        Raises:
            RuntimeError: If the process fails to start.
        """
        if self._started:
            return

        try:
            self._process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._env,
                text=True,
                bufsize=1,  # Line buffered
            )
            self._started = True
            logger.info(
                "StdioTransport started: %s (pid=%d)",
                " ".join(self._command), self._process.pid,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(
                f"Failed to start MCP server '{' '.join(self._command)}': {exc}"
            ) from exc

    async def send(self, message: dict) -> None:
        """Send a JSON-RPC message to the server.

        Args:
            message: JSON-serializable dict.
        """
        if not self._process or not self._process.stdin:
            raise RuntimeError("StdioTransport not started")

        line = json.dumps(message, ensure_ascii=False) + "\n"
        self._process.stdin.write(line)
        self._process.stdin.flush()

    async def receive(self) -> dict:
        """Receive a JSON-RPC response from the server.

        Blocks until a line is received on stdout.

        Returns:
            Parsed JSON response dict.

        Raises:
            RuntimeError: If transport is not started or process has died.
        """
        if not self._process or not self._process.stdout:
            raise RuntimeError("StdioTransport not started")

        while True:
            if self._process.poll() is not None:
                stderr = ""
                if self._process.stderr:
                    stderr = self._process.stderr.read()
                raise RuntimeError(
                    f"MCP process exited with code {self._process.returncode}: {stderr}"
                )

            line = self._process.stdout.readline()
            if not line:
                raise RuntimeError("MCP process closed stdout")

            line = line.strip()
            if line:
                try:
                    return json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "Invalid JSON from MCP server: %s", exc,
                    )
                    continue

    async def close(self) -> None:
        """Close the transport and terminate the child process."""
        if self._closed:
            return

        self._closed = True
        if self._process:
            try:
                self._process.stdin.close()
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait()
                logger.info("StdioTransport closed (pid=%d)", self._process.pid)
            except (OSError, ProcessLookupError):
                pass  # Process already gone

    @property
    def is_alive(self) -> bool:
        """True if transport is started and process is running."""
        return (
            self._started
            and not self._closed
            and self._process is not None
            and self._process.poll() is None
        )


class HttpTransport:
    """HTTP + SSE transport for remote MCP servers.

    Uses httpx for HTTP JSON-RPC and Server-Sent Events for streaming.
    """

    def __init__(self, url: str, headers: dict[str, str] | None = None):
        self._url = url.rstrip("/")
        self._headers = headers or {}
        self._connected = False

    async def connect(self) -> None:
        """Connect to the remote MCP server."""
        import httpx

        self._client = httpx.AsyncClient(
            base_url=self._url,
            headers=self._headers,
            timeout=30.0,
        )
        self._connected = True
        logger.info("HttpTransport connected to %s", self._url)

    async def send(self, message: dict) -> dict:
        """Send a JSON-RPC request over HTTP.

        Args:
            message: JSON-RPC request dict.

        Returns:
            JSON-RPC response dict.
        """
        if not self._connected:
            raise RuntimeError("HttpTransport not connected")

        import httpx

        try:
            response = await self._client.post(
                "/",
                json=message,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"HTTP request to MCP server failed: {exc}"
            ) from exc

    async def close(self) -> None:
        """Close the HTTP connection."""
        if hasattr(self, "_client") and self._connected:
            await self._client.aclose()
            self._connected = False
            logger.info("HttpTransport closed")

    @property
    def is_connected(self) -> bool:
        """True if connected to the server."""
        return self._connected
