"""Shared pytest fixtures for code_agent tests.

Fixtures:
- sample_token: A valid OAuth token dict for auth tests
- mock_chat_client: A mock ChatClient that yields pre-defined StreamEvent sequences
- tmp_workspace: A temporary directory for file-based tool tests
- tool_registry: A ToolRegistry with a simple echo tool registered
- mock_no_config_file: Autouse fixture — patches load_config to return None
  (prevents real ~/.evil-code/config.yaml from interfering with tests)
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from code_agent.api.schemas import StreamEvent, ToolCallDelta
from code_agent.core.messages import ToolCallItem
from code_agent.core.session import Session
from code_agent.tools.registry import ToolRegistry
from code_agent.tools.result import ToolResult


# ─── Auth fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def sample_token() -> dict:
    """A valid OAuth token dict (expires in 1 hour)."""
    return {
        "accessToken": "sk-test-access-token-12345",
        "refreshToken": "sk-test-refresh-token-67890",
        "expiresAt": int((time.time() + 3600) * 1000),
    }


@pytest.fixture
def expired_token() -> dict:
    """An expired OAuth token dict."""
    return {
        "accessToken": "sk-expired-access-token",
        "refreshToken": "sk-expired-refresh-token",
        "expiresAt": int((time.time() - 100) * 1000),
    }


# ─── Core fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def session() -> Session:
    """A fresh Session with a system prompt."""
    return Session(system_prompt="You are a helpful coding assistant.")


class MockChatClient:
    """Mock ChatClient that yields pre-defined StreamEvent sequences.

    Usage:
        client = MockChatClient()
        client.set_responses([
            [StreamEvent(kind="text_delta", text="Hello"), StreamEvent(kind="turn_end")],
        ])
    """

    def __init__(self):
        self._responses: list[list[StreamEvent]] = []
        self._call_index: int = 0
        self.call_history: list[dict] = []

    @property
    def model(self) -> str:
        """Model id reported to the Agent for usage ledger accounting."""
        return "test-model"

    def set_responses(self, responses: list[list[StreamEvent]]) -> None:
        """Set the sequence of responses (one list per chat_stream call)."""
        self._responses = responses
        self._call_index = 0

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kwargs,
    ):
        """Async generator yielding pre-defined events."""
        self.call_history.append({"messages": messages, "tools": tools})

        if self._call_index >= len(self._responses):
            # Default: return a simple text response
            events = [
                StreamEvent(kind="text_delta", text="No more responses configured."),
                StreamEvent(kind="turn_end"),
            ]
        else:
            events = self._responses[self._call_index]
            self._call_index += 1

        for event in events:
            yield event

    async def close(self) -> None:
        pass


@pytest.fixture
def mock_chat_client() -> MockChatClient:
    """A MockChatClient instance."""
    return MockChatClient()


# ─── Tools fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """A temporary workspace directory for file-based tool tests."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def tool_registry() -> ToolRegistry:
    """A ToolRegistry with a simple echo tool registered and finalized."""
    from pydantic import BaseModel, Field

    registry = ToolRegistry(max_output_chars=1000)

    class EchoInput(BaseModel):
        text: str = Field(description="Text to echo back")

    @registry.register("echo", EchoInput, description="Echo input text")
    async def echo_tool(input: EchoInput) -> ToolResult:
        return ToolResult.ok(f"Echo: {input.text}")

    registry.finalize()
    return registry


# ─── Helper factories ──────────────────────────────────────────────────────────


def make_text_response(text: str) -> list[StreamEvent]:
    """Create a simple text response event sequence."""
    return [
        StreamEvent(kind="text_delta", text=text),
        StreamEvent(kind="turn_end"),
    ]


def make_tool_call_response(
    call_id: str, tool_name: str, arguments: str
) -> list[StreamEvent]:
    """Create a tool call response event sequence (streaming fragments)."""
    return [
        StreamEvent(
            kind="tool_call_delta",
            tool_call=ToolCallDelta(index=0, id=call_id, name=tool_name),
        ),
        StreamEvent(
            kind="tool_call_delta",
            tool_call=ToolCallDelta(index=0, arguments_fragment=arguments),
        ),
        StreamEvent(kind="turn_end"),
    ]


# ─── Auth config isolation ───────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def mock_no_config_file(monkeypatch, request):
    """Prevent real ~/.evil-code/config.yaml from leaking into tests.

    Autouse — every test gets this unless it opts out by marking itself with
    ``@pytest.mark.use_real_config`` (config_store round-trip tests need the
    real load_config/save_config). Patches both the source function and the
    imported reference in cli/auth.py (load_config was relocated from
    cli/main.py to cli/auth.py).
    """
    if "use_real_config" not in request.keywords:
        monkeypatch.setattr(
            "code_agent.auth.config_store.load_config",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "code_agent.cli.auth.load_config",
            lambda *a, **kw: None,
        )
    yield
