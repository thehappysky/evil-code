"""Tests for the MCP layer: client.py, transport.py, adapter.py.

Strategy:
- McpClient is tested against a FAKE in-memory transport that records the
  JSON-RPC requests sent and returns canned responses. No real subprocesses
  are spawned and no network calls are made.
- StdioTransport framing/parsing helpers are tested with a mocked ``Popen``
  (MagicMock) process so no child process is spawned.
- HttpTransport send/close are tested with a mocked httpx client.
- register_mcp_tools is tested against a fake client object exposing the
  minimal interface the adapter needs (server_name / list_tools / call_tool).
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from code_agent.mcp.adapter import (
    _json_type_to_python,
    _schema_to_model,
    register_mcp_tools,
)
from code_agent.mcp.client import McpClient, McpServerConfig
from code_agent.mcp.transport import HttpTransport, StdioTransport
from code_agent.tools.registry import ToolRegistry


# ─── Test doubles ──────────────────────────────────────────────────────────────


class FakeTransport:
    """In-memory transport: records sent requests, returns canned responses.

    Duck-types the real transports: any constructor kwargs from
    ``McpClient.connect`` (command/env/url/headers) are accepted and ignored,
    so this class can be monkeypatched in place of ``StdioTransport`` or
    ``HttpTransport``.
    """

    def __init__(self, responses: list[dict] | None = None, **kwargs):
        self._responses = list(responses or [])
        self.sent: list[dict] = []
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def connect(self) -> None:
        self.started = True

    async def send(self, message: dict) -> None:
        self.sent.append(message)

    async def receive(self) -> dict:
        if not self._responses:
            raise AssertionError("FakeTransport.receive(): no canned responses left")
        return self._responses.pop(0)

    async def close(self) -> None:
        self.closed = True


class _SingletonFakeTransport(FakeTransport):
    """Constructor factory that returns a pre-configured FakeTransport.

    ``McpClient.connect`` builds its transport by calling ``StdioTransport`` /
    ``HttpTransport`` directly, so monkeypatching the transport class with a
    plain factory would break the ``isinstance`` handshake check. This class
    instead returns the shared ``_instance`` (a plain ``FakeTransport``) from
    ``__new__``; because the returned object is not an instance of the factory
    class, ``__init__`` is not re-run with the constructor's command/env args.
    """

    _instance: FakeTransport | None = None

    def __new__(cls, *args, **kwargs):
        return cls._instance


class FakeMcpClient:
    """Minimal fake for the adapter: server_name / list_tools / call_tool."""

    def __init__(self, name: str, tools: list[dict], call_results: dict | None = None):
        self._name = name
        self._tools = tools
        self._call_results = call_results or {}
        self.call_log: list[tuple[str, dict]] = []

    @property
    def server_name(self) -> str:
        return self._name

    async def list_tools(self) -> list[dict]:
        return self._tools

    async def call_tool(self, name: str, arguments: dict) -> dict:
        self.call_log.append((name, arguments))
        return self._call_results.get(
            name, {"content": [{"type": "text", "text": "ok"}]}
        )


# ─── Canned JSON-RPC payloads ──────────────────────────────────────────────────

INITIALIZE_RESPONSE = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {"listChanged": True}},
        "serverInfo": {"name": "fake-server", "version": "1.0"},
    },
}

TOOL_SEARCH = {
    "name": "search",
    "description": "Search issues",
    "inputSchema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Search query"}},
        "required": ["query"],
    },
}

TOOL_STATUS = {
    "name": "status",
    "description": "Repo status",
    "inputSchema": {"type": "object", "properties": {}},
}


# ─── McpClient: initialize / connect ──────────────────────────────────────────


class TestMcpClientConnect:
    async def test_connect_stdio_sends_initialize_and_parses(self, monkeypatch):
        """connect() performs the MCP handshake through the transport."""
        fake = FakeTransport(responses=[INITIALIZE_RESPONSE])
        _SingletonFakeTransport._instance = fake
        monkeypatch.setattr(
            "code_agent.mcp.client.StdioTransport", _SingletonFakeTransport
        )
        client = McpClient(
            McpServerConfig(name="test-server", command=["python", "server.py"])
        )

        await client.connect()

        # initialize request framing
        assert len(fake.sent) == 1
        initialize = fake.sent[0]
        assert initialize["jsonrpc"] == "2.0"
        assert initialize["id"] == 1
        assert initialize["method"] == "initialize"
        assert initialize["params"]["protocolVersion"] == "2024-11-05"
        assert initialize["params"]["capabilities"] == {}
        assert initialize["params"]["clientInfo"] == {
            "name": "evil-code",
            "version": "0.2.0",
        }

        # handshake result parsed into capabilities / server info
        assert client.is_connected is True
        assert client.server_name == "test-server"
        assert client.server_info["serverInfo"]["name"] == "fake-server"
        assert client.server_info["capabilities"]["tools"]["listChanged"] is True

    async def test_connect_http_transport(self, monkeypatch):
        """connect() works for the HTTP transport path too."""
        fake = FakeTransport(responses=[INITIALIZE_RESPONSE])
        _SingletonFakeTransport._instance = fake
        monkeypatch.setattr(
            "code_agent.mcp.client.HttpTransport", _SingletonFakeTransport
        )
        client = McpClient(
            McpServerConfig(name="http-srv", url="http://localhost:9999")
        )

        await client.connect()

        assert fake.started is True
        assert fake.sent[0]["method"] == "initialize"
        assert client.is_connected is True
        assert client.server_info["serverInfo"]["name"] == "fake-server"

    async def test_connect_initialize_error_raises(self, monkeypatch):
        """An initialize error response surfaces as RuntimeError."""
        fake = FakeTransport(
            responses=[
                {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "boom"}}
            ]
        )
        _SingletonFakeTransport._instance = fake
        monkeypatch.setattr(
            "code_agent.mcp.client.StdioTransport", _SingletonFakeTransport
        )
        client = McpClient(McpServerConfig(name="srv", command=["x"]))

        with pytest.raises(RuntimeError, match="MCP initialize failed"):
            await client.connect()
        assert client.is_connected is False

    async def test_connect_without_command_or_url_raises(self):
        """Missing command and url raises ValueError."""
        client = McpClient(McpServerConfig(name="srv"))
        with pytest.raises(ValueError, match="either 'command' or 'url'"):
            await client.connect()


# ─── McpClient: tools/list ─────────────────────────────────────────────────────


class TestMcpClientListTools:
    @staticmethod
    def _make_client(fake: FakeTransport) -> McpClient:
        client = McpClient(McpServerConfig(name="srv"))
        client._transport = fake
        client._connected = True
        return client

    async def test_list_tools_parses_tools(self):
        """tools/list response tools are parsed into dicts."""
        fake = FakeTransport(
            responses=[{"jsonrpc": "2.0", "id": 1, "result": {"tools": [TOOL_SEARCH]}}]
        )
        client = self._make_client(fake)

        tools = await client.list_tools()

        assert len(tools) == 1
        assert tools[0]["name"] == "search"
        assert tools[0]["description"] == "Search issues"
        assert "inputSchema" in tools[0]
        # request carries the tools/list method
        req = fake.sent[0]
        assert req["method"] == "tools/list"
        assert req["params"] == {}

    async def test_list_tools_cursor_pagination(self):
        """nextCursor triggers a follow-up request carrying the cursor."""
        fake = FakeTransport(
            responses=[
                {"jsonrpc": "2.0", "id": 1, "result": {"tools": [TOOL_SEARCH], "nextCursor": "abc"}},
                {"jsonrpc": "2.0", "id": 2, "result": {"tools": [TOOL_STATUS]}},
            ]
        )
        client = self._make_client(fake)

        tools = await client.list_tools()

        assert [t["name"] for t in tools] == ["search", "status"]
        assert len(fake.sent) == 2
        assert fake.sent[0]["id"] == 1
        assert fake.sent[1]["id"] == 2
        assert fake.sent[1]["params"] == {"cursor": "abc"}

    async def test_list_tools_no_next_cursor_stops(self):
        """Missing nextCursor terminates pagination loop."""
        fake = FakeTransport(
            responses=[{"jsonrpc": "2.0", "id": 1, "result": {"tools": [TOOL_SEARCH]}}]
        )
        client = self._make_client(fake)

        tools = await client.list_tools()
        assert len(tools) == 1
        assert len(fake.sent) == 1

    async def test_list_tools_error_raises(self):
        """JSON-RPC error response surfaces as RuntimeError, not ignored."""
        fake = FakeTransport(
            responses=[
                {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "method not found"}}
            ]
        )
        client = self._make_client(fake)

        with pytest.raises(RuntimeError, match="tools/list failed"):
            await client.list_tools()

    async def test_list_tools_not_connected(self):
        """Calling list_tools before connect raises."""
        client = McpClient(McpServerConfig(name="srv"))
        with pytest.raises(RuntimeError, match="not connected"):
            await client.list_tools()


# ─── McpClient: tools/call ─────────────────────────────────────────────────────


class TestMcpClientCallTool:
    @staticmethod
    def _make_client(fake: FakeTransport) -> McpClient:
        client = McpClient(McpServerConfig(name="srv"))
        client._transport = fake
        client._connected = True
        return client

    async def test_call_tool_sends_name_and_arguments(self):
        """tools/call request carries name, arguments and id correlation."""
        fake = FakeTransport(
            responses=[
                {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "ok"}]}}
            ]
        )
        client = self._make_client(fake)

        result = await client.call_tool("search", {"query": "issue"})

        req = fake.sent[0]
        assert req["method"] == "tools/call"
        assert req["id"] == 1
        assert req["params"] == {"name": "search", "arguments": {"query": "issue"}}
        assert result == {"content": [{"type": "text", "text": "ok"}]}

    async def test_call_tool_id_increments_after_initialize(self):
        """Request ids are monotonically incremented across calls."""
        fake = FakeTransport(
            responses=[
                {"jsonrpc": "2.0", "id": 1, "result": {}},
                {"jsonrpc": "2.0", "id": 2, "result": {}},
            ]
        )
        client = self._make_client(fake)

        await client.call_tool("a", {})
        await client.call_tool("b", {})
        assert [m["id"] for m in fake.sent] == [1, 2]

    async def test_call_tool_is_error_field_parsed(self):
        """Non-zero isError in the result is surfaced, not swallowed."""
        fake = FakeTransport(
            responses=[
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"content": [{"type": "text", "text": "failed"}], "isError": True},
                }
            ]
        )
        client = self._make_client(fake)

        result = await client.call_tool("boom", {})
        assert result["isError"] is True
        assert result["content"][0]["text"] == "failed"

    async def test_call_tool_error_response_raises(self):
        """JSON-RPC error response surfaces as RuntimeError with the message."""
        fake = FakeTransport(
            responses=[
                {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "bad params"}}
            ]
        )
        client = self._make_client(fake)

        with pytest.raises(RuntimeError, match="bad params"):
            await client.call_tool("search", {})

    async def test_call_tool_not_connected(self):
        """Calling call_tool before connect raises."""
        client = McpClient(McpServerConfig(name="srv"))
        with pytest.raises(RuntimeError, match="not connected"):
            await client.call_tool("x", {})

    async def test_close_closes_transport(self):
        """close() delegates to the transport and resets state."""
        fake = FakeTransport(responses=[])
        client = self._make_client(fake)

        await client.close()

        assert fake.closed is True
        assert client.is_connected is False
        assert client._transport is None


# ─── StdioTransport: framing / parsing ────────────────────────────────────────


def _started_stdio() -> StdioTransport:
    """A StdioTransport with a mocked process (no real subprocess spawned)."""
    t = StdioTransport(command=["echo", "hi"])
    t._process = MagicMock()
    t._process.pid = 1234
    t._started = True
    return t


class TestStdioTransport:
    async def test_send_not_started_raises(self):
        t = StdioTransport(command=["echo", "hi"])
        with pytest.raises(RuntimeError, match="not started"):
            await t.send({"id": 1})

    async def test_receive_not_started_raises(self):
        t = StdioTransport(command=["echo", "hi"])
        with pytest.raises(RuntimeError, match="not started"):
            await t.receive()

    async def test_start_spawns_process(self, monkeypatch):
        """start() opens the subprocess via subprocess.Popen (mocked)."""
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 4242
        monkeypatch.setattr("subprocess.Popen", MagicMock(return_value=proc))
        t = StdioTransport(command=["python", "-c", "pass"])

        await t.start()
        assert t.is_alive is True
        # idempotent: a second start is a no-op
        await t.start()
        assert subprocess.Popen.call_count == 1

    async def test_start_popen_error_raises(self, monkeypatch):
        """A Popen OSError surfaces as RuntimeError with the command."""

        def _raise(*args, **kwargs):
            raise OSError("no such file")

        monkeypatch.setattr("subprocess.Popen", _raise)
        t = StdioTransport(command=["does-not-exist"])

        with pytest.raises(RuntimeError, match="Failed to start MCP server"):
            await t.start()

    async def test_send_frames_newline_delimited_json(self):
        """send() writes JSON + newline to stdin (newline framing, not Content-Length)."""
        t = _started_stdio()
        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

        await t.send(msg)

        expected = json.dumps(msg, ensure_ascii=False) + "\n"
        t._process.stdin.write.assert_called_once_with(expected)
        t._process.stdin.flush.assert_called_once()

    async def test_receive_parses_json_line(self):
        """receive() reads one stdout line and parses it as JSON."""
        t = _started_stdio()
        t._process.poll.return_value = None
        t._process.stdout.readline.side_effect = ['{"jsonrpc":"2.0","id":1,"result":{}}\n']

        resp = await t.receive()
        assert resp == {"jsonrpc": "2.0", "id": 1, "result": {}}

    async def test_receive_skips_invalid_json_lines(self):
        """Invalid JSON lines are skipped; the next valid line is parsed."""
        t = _started_stdio()
        t._process.poll.return_value = None
        t._process.stdout.readline.side_effect = ["garbage\n", '{"jsonrpc":"2.0","id":2}\n']

        resp = await t.receive()
        assert resp == {"jsonrpc": "2.0", "id": 2}

    async def test_receive_process_exited_raises(self):
        """A dead process surfaces as RuntimeError including stderr."""
        t = _started_stdio()
        t._process.poll.return_value = 1
        t._process.returncode = 1
        t._process.stderr.read.return_value = "crash trace"

        with pytest.raises(RuntimeError, match="exited with code 1"):
            await t.receive()

    async def test_receive_stdout_closed_raises(self):
        """EOF on stdout surfaces as RuntimeError."""
        t = _started_stdio()
        t._process.poll.return_value = None
        t._process.stdout.readline.return_value = ""

        with pytest.raises(RuntimeError, match="closed stdout"):
            await t.receive()

    async def test_close_terminates_and_is_idempotent(self):
        """close() closes stdin and terminates the child (mocked)."""
        t = _started_stdio()

        await t.close()
        t._process.stdin.close.assert_called_once()
        t._process.terminate.assert_called_once()
        t._process.wait.assert_called_once()
        assert t.is_alive is False

        await t.close()  # no-op on second call
        assert t._process.terminate.call_count == 1

    def test_is_alive_state_transitions(self):
        """is_alive reflects started/closed/process state."""
        t = StdioTransport(command=["x"])
        assert t.is_alive is False

        t._started = True
        t._process = MagicMock()
        t._process.poll.return_value = None
        assert t.is_alive is True

        t._process.poll.return_value = 0
        assert t.is_alive is False

        t._process.poll.return_value = None
        t._closed = True
        assert t.is_alive is False


# ─── HttpTransport ────────────────────────────────────────────────────────────


class TestHttpTransport:
    async def test_connect_creates_client(self):
        """connect() builds an httpx client and flips is_connected."""
        t = HttpTransport(url="http://localhost:8080/", headers={"X-Test": "1"})
        assert t.is_connected is False

        await t.connect()
        assert t.is_connected is True
        assert t._url == "http://localhost:8080"  # trailing slash stripped

        await t.close()
        assert t.is_connected is False

    async def test_send_not_connected_raises(self):
        t = HttpTransport(url="http://localhost:8080")
        with pytest.raises(RuntimeError, match="not connected"):
            await t.send({"id": 1})

    async def test_send_posts_json_and_returns_response(self):
        """send() POSTs the JSON-RPC message and returns parsed JSON."""
        t = HttpTransport(url="http://localhost:8080")
        t._connected = True
        t._client = MagicMock()
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        t._client.post = AsyncMock(return_value=resp)
        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

        out = await t.send(msg)

        assert out == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        t._client.post.assert_called_once_with("/", json=msg)

    async def test_send_http_error_raises(self):
        """An httpx HTTPError surfaces as RuntimeError (no network call)."""
        import httpx

        t = HttpTransport(url="http://localhost:8080")
        t._connected = True
        t._client = MagicMock()
        t._client.post.side_effect = httpx.ConnectError("connection refused")

        with pytest.raises(RuntimeError, match="HTTP request to MCP server failed"):
            await t.send({"id": 1})

    async def test_close_when_never_connected_is_noop(self):
        t = HttpTransport(url="http://localhost:8080")
        await t.close()
        assert t.is_connected is False


# ─── adapter.register_mcp_tools ───────────────────────────────────────────────


class TestRegisterMcpTools:
    async def test_registers_tools_and_returns_count(self):
        """Tools are registered as '{server}__{tool}' and the count returned."""
        registry = ToolRegistry()
        fake = FakeMcpClient(
            name="github",
            tools=[TOOL_SEARCH, TOOL_STATUS],
        )

        count = await register_mcp_tools(registry, fake)

        assert count == 2
        names = registry.list_names()
        assert "github__search" in names
        assert "github__status" in names

        defs = {d["function"]["name"]: d for d in registry.get_definitions()}
        assert defs["github__search"]["function"]["description"] == "Search issues"
        assert defs["github__status"]["function"]["description"] == "Repo status"

    async def test_handler_delegates_to_client(self):
        """The registered handler forwards arguments to client.call_tool."""
        registry = ToolRegistry()
        fake = FakeMcpClient(
            name="github",
            tools=[TOOL_SEARCH],
            call_results={"search": {"content": [{"type": "text", "text": "found: 3"}]}},
        )
        await register_mcp_tools(registry, fake)

        result = await registry.execute("github__search", {"query": "issues"})

        assert result.is_error is False
        assert result.output == "found: 3"
        assert fake.call_log == [("search", {"query": "issues"})]

    async def test_multiline_content_joined(self):
        """Multiple content parts are joined with newlines."""
        registry = ToolRegistry()
        fake = FakeMcpClient(
            name="db",
            tools=[{"name": "query", "description": "Q", "inputSchema": {}}],
            call_results={"query": {"content": [{"text": "row1"}, {"text": "row2"}]}},
        )
        await register_mcp_tools(registry, fake)

        result = await registry.execute("db__query", {})
        assert result.output == "row1\nrow2"

    async def test_handler_returns_error_on_client_failure(self):
        """A client.call_tool exception becomes a ToolResult.error."""
        registry = ToolRegistry()
        fake = FakeMcpClient(
            name="github",
            tools=[{"name": "boom", "description": "B", "inputSchema": {}}],
        )

        async def _failing(name, arguments):
            raise RuntimeError("server down")

        fake.call_tool = _failing
        await register_mcp_tools(registry, fake)

        result = await registry.execute("github__boom", {})
        assert result.is_error is True
        assert "github/boom" in result.output

    async def test_list_tools_failure_returns_zero(self):
        """A list_tools failure is logged and returns 0."""
        registry = ToolRegistry()
        fake = FakeMcpClient(name="github", tools=[])

        async def _failing():
            raise RuntimeError("list failed")

        fake.list_tools = _failing

        assert await register_mcp_tools(registry, fake) == 0

    async def test_skips_tool_without_name(self):
        """Tools with an empty/missing name are skipped."""
        registry = ToolRegistry()
        fake = FakeMcpClient(name="github", tools=[{"description": "no name"}])

        assert await register_mcp_tools(registry, fake) == 0
        assert registry.list_names() == []

    async def test_default_description(self):
        """A tool without a description gets the default text."""
        registry = ToolRegistry()
        fake = FakeMcpClient(
            name="github",
            tools=[{"name": "plain", "inputSchema": {}}],
        )
        await register_mcp_tools(registry, fake)

        defs = registry.get_definitions()
        assert defs[0]["function"]["description"] == "MCP tool from github"

    async def test_is_error_propagates_to_tool_result(self):
        """MCP result.isError should surface as ToolResult.is_error."""
        registry = ToolRegistry()
        fake = FakeMcpClient(
            name="github",
            tools=[{"name": "flaky", "description": "F", "inputSchema": {}}],
        )

        async def _erroring(name, arguments):
            return {"content": [{"type": "text", "text": "boom"}], "isError": True}

        fake.call_tool = _erroring
        await register_mcp_tools(registry, fake)

        result = await registry.execute("github__flaky", {})
        assert result.is_error is True
        assert "boom" in result.output


# ─── adapter._schema_to_model / _json_type_to_python ──────────────────────────


class TestSchemaToModel:
    def test_empty_schema_falls_back_to_kwargs(self):
        """No properties → a model with a single 'kwargs' dict field."""
        model = _schema_to_model("Empty", {})
        assert issubclass(model, BaseModel)
        assert model().kwargs == {}
        inst = model(kwargs={"foo": 1, "bar": "x"})
        assert inst.kwargs == {"foo": 1, "bar": "x"}

    def test_properties_become_fields(self):
        """Schema properties map to required/optional model fields."""
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "string", "description": "opt"},
                "n": {"type": "integer", "description": "req"},
            },
            "required": ["n"],
        }
        model = _schema_to_model("Fields", schema)
        inst = model(a="x", n=5)
        assert inst.a == "x"
        assert inst.n == 5

    def test_json_type_mapping(self):
        """JSON Schema types map to Python types, unknown → str."""
        assert _json_type_to_python("string") is str
        assert _json_type_to_python("integer") is int
        assert _json_type_to_python("number") is float
        assert _json_type_to_python("boolean") is bool
        assert _json_type_to_python("array") is list
        assert _json_type_to_python("object") is dict
        assert _json_type_to_python("weird") is str
