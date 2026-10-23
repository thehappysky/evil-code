"""Custom exception types for the Agent system.

Flat inheritance hierarchy (one level only).
Each exception carries contextual data for precise error reporting.
"""

from __future__ import annotations


class AgentError(Exception):
    """Base class for all Agent-specific exceptions."""

    pass


class MaxTurnsReached(AgentError):
    """Raised when the Agent exceeds its maximum reasoning turns.

    Attributes:
        max_turns: The limit that was reached.
    """

    def __init__(self, max_turns: int):
        self.max_turns = max_turns
        super().__init__(
            f"Agent reached maximum turns limit ({max_turns}). "
            f"Consider increasing --max-turns or simplifying the request."
        )


class AuthenticationError(AgentError):
    """Raised when authentication fails (no token, invalid token, refresh rejected).

    The Agent loop catches this to terminate the current turn and report
    to the user. No silent degradation — an LLM call without valid auth
    will always fail.
    """

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message)


class ToolNotFoundError(AgentError):
    """Raised when the model calls a tool that doesn't exist in the registry.

    Attributes:
        tool_name: The name of the tool that was not found.
    """

    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        super().__init__(f"Tool '{tool_name}' not found in registry")
