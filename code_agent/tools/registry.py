"""Tool registry - registration, lookup, and dispatch center.

Logic reference: Rust version ToolRegistryBuilder + FinalizedToolset
(registry/types.rs).

Design:
- Decorator registration: equivalent to Rust's register::<T>()
- finalize() lock: equivalent to Rust's FinalizedToolset immutability
- Pydantic validation: model_validate() replaces Rust's serde_path_to_error
- Unified truncation: all results pass through _maybe_truncate()

Dependencies: tools.definition, tools.result, core.exceptions, helpers.text_truncate
Does NOT depend on: Agent, Session, CLI.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from pydantic import BaseModel, ValidationError

from code_agent.core.exceptions import ToolNotFoundError
from code_agent.helpers.text_truncate import truncate_with_preview
from code_agent.tools.definition import ToolDefinition
from code_agent.tools.result import ToolResult

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Tool registration and dispatch center.

    Usage:
        registry = ToolRegistry()

        @registry.register("read_file", ReadFileInput, description="...")
        async def read_file(input: ReadFileInput) -> str: ...

        registry.finalize()  # Lock — no more registrations

        result = await registry.execute("read_file", {"file_path": "..."})
    """

    def __init__(self, max_output_chars: int = 100_000):
        self._tools: dict[str, ToolDefinition] = {}
        self._finalized: bool = False
        self._max_output_chars = max_output_chars

    def register(
        self,
        name: str,
        input_model: type[BaseModel],
        *,
        description: str,
        category: str | None = None,
        safety: str | None = None,
    ) -> Callable:
        """Decorator: register a tool. Must be called before finalize().

        Args:
            name: Tool name (model-visible).
            input_model: Pydantic model for input validation + schema generation.
            description: Tool description (model-visible).
            category: Optional category tag.
            safety: Optional safety classification declared by the tool
                ("read" / "control" / "write" / "execute"). [GRD-03] Replaces
                hardcoded tool-name sets in permission/manager.py.

        Returns:
            Decorator function that registers the handler.

        Raises:
            RuntimeError: If called after finalize().
        """
        if self._finalized:
            raise RuntimeError(
                f"Cannot register tool '{name}': registry is finalized"
            )

        def decorator(fn: Callable) -> Callable:
            if name in self._tools:
                logger.warning(f"Tool '{name}' re-registered (overwriting)")
            self._tools[name] = ToolDefinition(
                name=name,
                description=description,
                input_model=input_model,
                handler=fn,
                category=category,
                safety=safety,
            )
            return fn

        return decorator

    def finalize(self) -> None:
        """Lock the registry. No more registrations allowed after this.

        Equivalent to Rust's FinalizedToolset immutability semantics.
        """
        self._finalized = True
        logger.debug(
            f"ToolRegistry finalized with {len(self._tools)} tools: "
            f"{list(self._tools.keys())}"
        )

    def get_definitions(self) -> list[dict]:
        """Return all tool definitions in OpenAI format.

        Called every turn (not cached) to ensure newly registered
        MCP tools are immediately available.
        """
        return [tool.to_openai_schema() for tool in self._tools.values()]

    async def execute(self, name: str, arguments: dict) -> ToolResult:
        """Execute a tool by name with given arguments.

        Steps:
        1. Lookup tool -> ToolNotFoundError if not found
        2. Pydantic model_validate() -> ToolResult.error() on validation failure
        3. Call handler -> exception capture -> ToolResult.error()
        4. Unified format + truncation -> return ToolResult

        Args:
            name: Tool name to execute.
            arguments: Raw argument dict (from JSON parsing of model output).

        Returns:
            ToolResult with output text, error flag, and truncation flag.

        Raises:
            ToolNotFoundError: If tool name is not in registry.
        """
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(name)

        # Validate arguments with Pydantic
        try:
            validated_input = tool.input_model.model_validate(arguments)
        except ValidationError as exc:
            return ToolResult.error(
                f"Invalid arguments for '{name}': {exc.error_count()} error(s)\n"
                f"{exc.errors()}"
            )

        # Execute handler
        try:
            result = await tool.handler(validated_input)
        except Exception as exc:
            logger.exception(f"Tool '{name}' execution failed")
            return ToolResult.error(f"Tool execution failed: {exc}")

        # Normalize result to ToolResult
        if isinstance(result, ToolResult):
            tool_result = result
        elif isinstance(result, str):
            tool_result = ToolResult.ok(result)
        else:
            tool_result = ToolResult.ok(str(result))

        # Apply unified truncation
        return self._maybe_truncate(tool_result)

    def list_names(self) -> list[str]:
        """Return list of registered tool names."""
        return list(self._tools.keys())

    def names_by_safety(self, safety: str) -> frozenset[str]:
        """Return tool names registered with the given safety classification.

        [GRD-03] Lets permission/manager.py query tool safety from the
        registry instead of hardcoding tool-name sets.
        """
        return frozenset(
            name for name, tool in self._tools.items() if tool.safety == safety
        )

    def _maybe_truncate(self, result: ToolResult) -> ToolResult:
        """Apply output truncation if exceeding max_output_chars."""
        if result.is_error:
            return result  # Don't truncate error messages

        if len(result.output) <= self._max_output_chars:
            return result

        truncated_text, was_truncated = truncate_with_preview(
            result.output,
            max_chars=self._max_output_chars,
            hint="Use file_read with offset to see more",
        )
        return ToolResult(
            output=truncated_text,
            is_error=False,
            truncated=was_truncated,
        )
