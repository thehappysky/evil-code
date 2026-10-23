"""Tests for tools/registry.py: ToolRegistry registration, dispatch, truncation.

Covers:
- register + finalize lock
- execute: valid / invalid args / missing tool
- get_definitions: OpenAI format output
- _maybe_truncate: output exceeding max_output_chars
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from code_agent.core.exceptions import ToolNotFoundError
from code_agent.tools.registry import ToolRegistry
from code_agent.tools.result import ToolResult


# ─── Test models ───────────────────────────────────────────────────────────────


class SimpleInput(BaseModel):
    text: str = Field(description="Input text")


class MultiFieldInput(BaseModel):
    name: str
    count: int = 1
    flag: bool = False


# ─── Registration ──────────────────────────────────────────────────────────────


class TestRegistration:
    def test_register_and_list(self):
        """Registered tools appear in list_names()."""
        reg = ToolRegistry()

        @reg.register("tool_a", SimpleInput, description="Tool A")
        async def tool_a(input: SimpleInput) -> ToolResult:
            return ToolResult.ok(input.text)

        assert "tool_a" in reg.list_names()

    def test_finalize_locks_registration(self):
        """Cannot register after finalize()."""
        reg = ToolRegistry()

        @reg.register("tool_a", SimpleInput, description="Tool A")
        async def tool_a(input: SimpleInput) -> ToolResult:
            return ToolResult.ok(input.text)

        reg.finalize()

        with pytest.raises(RuntimeError, match="finalized"):
            @reg.register("tool_b", SimpleInput, description="Tool B")
            async def tool_b(input: SimpleInput) -> ToolResult:
                return ToolResult.ok(input.text)

    def test_duplicate_register_overwrites(self):
        """Re-registering same name overwrites (with warning)."""
        reg = ToolRegistry()

        @reg.register("dup", SimpleInput, description="First")
        async def first(input: SimpleInput) -> ToolResult:
            return ToolResult.ok("first")

        @reg.register("dup", SimpleInput, description="Second")
        async def second(input: SimpleInput) -> ToolResult:
            return ToolResult.ok("second")

        assert reg.list_names().count("dup") == 1

    def test_get_definitions_openai_format(self):
        """get_definitions returns OpenAI function-calling format."""
        reg = ToolRegistry()

        @reg.register("my_tool", SimpleInput, description="Does things")
        async def my_tool(input: SimpleInput) -> ToolResult:
            return ToolResult.ok("done")

        reg.finalize()
        defs = reg.get_definitions()

        assert len(defs) == 1
        assert defs[0]["type"] == "function"
        assert defs[0]["function"]["name"] == "my_tool"
        assert defs[0]["function"]["description"] == "Does things"
        assert "properties" in defs[0]["function"]["parameters"]


# ─── Execution ─────────────────────────────────────────────────────────────────


class TestExecution:
    async def test_execute_valid(self):
        """Execute with valid arguments returns tool output."""
        reg = ToolRegistry()

        @reg.register("echo", SimpleInput, description="Echo")
        async def echo(input: SimpleInput) -> ToolResult:
            return ToolResult.ok(f"Echo: {input.text}")

        reg.finalize()
        result = await reg.execute("echo", {"text": "hello"})

        assert result.is_error is False
        assert result.output == "Echo: hello"

    async def test_execute_missing_tool(self):
        """Execute unknown tool raises ToolNotFoundError."""
        reg = ToolRegistry()
        reg.finalize()

        with pytest.raises(ToolNotFoundError):
            await reg.execute("nonexistent", {})

    async def test_execute_invalid_arguments(self):
        """Execute with invalid arguments returns error ToolResult (not exception)."""
        reg = ToolRegistry()

        @reg.register("strict", MultiFieldInput, description="Strict")
        async def strict(input: MultiFieldInput) -> ToolResult:
            return ToolResult.ok("ok")

        reg.finalize()
        # Missing required 'name' field
        result = await reg.execute("strict", {"count": 5})

        assert result.is_error is True
        assert "Invalid arguments" in result.output

    async def test_execute_handler_exception(self):
        """Handler exception is caught and returned as error ToolResult."""
        reg = ToolRegistry()

        @reg.register("boom", SimpleInput, description="Explodes")
        async def boom(input: SimpleInput) -> ToolResult:
            raise ValueError("Something went wrong")

        reg.finalize()
        result = await reg.execute("boom", {"text": "trigger"})

        assert result.is_error is True
        assert "Something went wrong" in result.output

    async def test_execute_string_return_normalized(self):
        """Handler returning plain string is normalized to ToolResult."""
        reg = ToolRegistry()

        @reg.register("str_tool", SimpleInput, description="Returns str")
        async def str_tool(input: SimpleInput) -> str:
            return f"plain: {input.text}"

        reg.finalize()
        result = await reg.execute("str_tool", {"text": "hi"})

        assert isinstance(result, ToolResult)
        assert result.output == "plain: hi"
        assert result.is_error is False


# ─── Truncation ────────────────────────────────────────────────────────────────


class TestTruncation:
    async def test_output_within_limit_not_truncated(self):
        """Output under max_output_chars is not truncated."""
        reg = ToolRegistry(max_output_chars=100)

        @reg.register("small", SimpleInput, description="Small output")
        async def small(input: SimpleInput) -> ToolResult:
            return ToolResult.ok("x" * 50)

        reg.finalize()
        result = await reg.execute("small", {"text": "a"})

        assert result.truncated is False
        assert len(result.output) == 50

    async def test_output_exceeding_limit_truncated(self):
        """Output over max_output_chars is truncated with footer."""
        reg = ToolRegistry(max_output_chars=100)

        @reg.register("big", SimpleInput, description="Big output")
        async def big(input: SimpleInput) -> ToolResult:
            return ToolResult.ok("y" * 500)

        reg.finalize()
        result = await reg.execute("big", {"text": "a"})

        assert result.truncated is True
        assert len(result.output) <= 200  # truncated + footer

    async def test_error_not_truncated(self):
        """Error results are never truncated."""
        reg = ToolRegistry(max_output_chars=50)

        @reg.register("err", SimpleInput, description="Error tool")
        async def err(input: SimpleInput) -> ToolResult:
            return ToolResult.error("e" * 200)

        reg.finalize()
        result = await reg.execute("err", {"text": "a"})

        assert result.is_error is True
        assert len(result.output) == 200  # Not truncated
