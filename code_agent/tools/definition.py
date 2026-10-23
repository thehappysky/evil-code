"""Tool definition data model.

Logic reference: Rust version FinalizedTool (registry/types.rs:405-439).

Rust version needs 7 closures; Python simplifies to 3 core fields:
- input_model: provides validation + JSON schema automatically (Pydantic)
- handler: execution logic
- description: model-visible description for decision-making

Does NOT depend on: ToolRegistry, Agent, CLI.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel


class ToolDefinition:
    """Complete description of a single tool.

    Attributes:
        name: Tool name (visible to model).
        description: Tool description (visible to model, used for call decisions).
        input_model: Pydantic model class (auto-generates JSON Schema + runtime validation).
        handler: Async execution function: async def fn(input: BaseModel) -> str | ToolResult.
        category: Optional category tag for grouping.
        safety: Optional safety classification declared by the tool itself.
            One of "read" / "control" / "write" / "execute" / None.
            [GRD-03] Replaces hardcoded tool-name sets in permission/manager.py.
    """

    def __init__(
        self,
        name: str,
        description: str,
        input_model: type[BaseModel],
        handler: Callable[..., Any],
        category: str | None = None,
        safety: str | None = None,
    ):
        self.name = name
        self.description = description
        self.input_model = input_model
        self.handler = handler
        self.category = category
        self.safety = safety

    def to_openai_schema(self) -> dict:
        """Convert to OpenAI function-calling format.

        Returns:
            Dict matching OpenAI's tool definition schema:
            {
                "type": "function",
                "function": {
                    "name": "...",
                    "description": "...",
                    "parameters": { JSON Schema }
                }
            }
        """
        schema = self.input_model.model_json_schema()
        # Remove Pydantic-specific fields that OpenAI doesn't need
        schema.pop("title", None)
        schema.pop("$defs", None)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }

    def __repr__(self) -> str:
        return f"ToolDefinition(name={self.name!r}, category={self.category!r})"
