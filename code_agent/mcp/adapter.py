# [Phase 4] 2026-07-29: MCP adapter — register MCP tools into ToolRegistry
"""MCP tool to ToolRegistry adapter.

References:
    - design-docs/Python-Agent-MVP-Phase4-详细设计.md §3 mcp/adapter.py

Naming convention: {server_name}__{tool_name}
Double underscore separates server name from tool name.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, create_model

from code_agent.tools.result import ToolResult

if TYPE_CHECKING:
    from code_agent.mcp.client import McpClient
    from code_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


async def register_mcp_tools(
    registry: "ToolRegistry",
    mcp_client: "McpClient",
) -> int:
    """Register all MCP server tools into the ToolRegistry.

    Each MCP tool is wrapped to:
    1. Forward arguments to MCP client.call_tool()
    2. Convert MCP response format to ToolResult
    3. Report errors with server name context

    Naming: {server_name}__{tool_name}
    e.g., "github__search_issues", "postgres__query"

    Args:
        registry: The ToolRegistry to register tools into.
        mcp_client: Connected MCP client.

    Returns:
        Number of tools successfully registered.
    """
    server_name = mcp_client.server_name
    count = 0

    try:
        tools = await mcp_client.list_tools()
    except Exception as exc:
        logger.error(
            "Failed to list tools from MCP server '%s': %s",
            server_name, exc,
        )
        return 0

    for tool in tools:
        tool_name = tool.get("name", "")
        if not tool_name:
            continue

        registered_name = f"{server_name}__{tool_name}"
        description = tool.get("description", f"MCP tool from {server_name}")
        input_schema = tool.get("inputSchema", {})

        # Create a dynamic Pydantic model from the tool's input schema
        input_model = _schema_to_model(
            f"{server_name}_{tool_name}_input",
            input_schema,
        )

        # Create an async handler that delegates to MCP
        async def make_handler(name=tool_name, client=mcp_client):
            async def handler(input):
                try:
                    result = await client.call_tool(
                        name, input.model_dump(),
                    )
                    # MCP protocol: isError=True means the tool executed but
                    # reported a failure — surface it as an error ToolResult so
                    # the agent loop sees the tool actually failed.
                    if result.get("isError"):
                        return ToolResult.error(
                            f"MCP tool '{server_name}/{name}' returned an error:\n"
                            + _extract_text(result)
                        )
                    return _extract_text(result)
                except Exception as exc:
                    return ToolResult.error(
                        f"MCP tool '{server_name}/{name}' failed: {exc}"
                    )
            return handler

        try:
            registry.register(
                registered_name,
                input_model,
                description=description,
                category=f"mcp:{server_name}",
            )(await make_handler())
            count += 1
            logger.debug(
                "Registered MCP tool: %s (%s/%s)",
                registered_name, server_name, tool_name,
            )
        except Exception as exc:
            logger.warning(
                "Failed to register MCP tool '%s': %s",
                registered_name, exc,
            )

    logger.info(
        "Registered %d MCP tools from '%s'",
        count, server_name,
    )
    return count


def _extract_text(result: dict) -> str:
    """Join the text parts of an MCP tool result into a single string."""
    content = result.get("content", [])
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict):
                text_parts.append(item.get("text", str(item)))
            else:
                text_parts.append(str(item))
        return "\n".join(text_parts)
    return str(content)


def _schema_to_model(name: str, schema: dict) -> type[BaseModel]:
    """Convert JSON Schema to a Pydantic model.

    Handles top-level "properties" and "required" fields.
    For complex schemas, falls back to a generic AnyParams model.

    Args:
        name: Model class name.
        schema: JSON Schema dict.

    Returns:
        Pydantic model class.
    """
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    if not properties:
        # No schema → accept any kwargs
        return create_model(
            name,
            __base__=BaseModel,
            **{"kwargs": (dict, Field(default_factory=dict))},
        )

    fields: dict = {}
    for prop_name, prop_schema in properties.items():
        prop_type = _json_type_to_python(prop_schema.get("type", "string"))
        description = prop_schema.get("description", "")
        default = ... if prop_name in required else None
        fields[prop_name] = (prop_type, Field(default=default, description=description))

    return create_model(name, __base__=BaseModel, **fields)


def _json_type_to_python(json_type: str):
    """Map JSON Schema type to Python type.

    Args:
        json_type: JSON Schema type string.

    Returns:
        Python type.
    """
    type_map = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    return type_map.get(json_type, str)
