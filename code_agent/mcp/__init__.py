# [Phase 4] 2026-07-29: MCP package
"""MCP (Model Context Protocol) integration package."""

from code_agent.mcp.client import McpClient, McpServerConfig
from code_agent.mcp.adapter import register_mcp_tools
from code_agent.mcp.transport import HttpTransport, StdioTransport

__all__ = [
    "McpClient",
    "McpServerConfig",
    "register_mcp_tools",
    "StdioTransport",
    "HttpTransport",
]
