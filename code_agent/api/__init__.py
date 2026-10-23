"""API communication layer - LLM HTTP client.

Note: ChatClient is NOT re-exported here to avoid circular imports
(ChatClient -> core.exceptions -> core/__init__).
Import directly: from code_agent.api.client import ChatClient
"""

from code_agent.api.schemas import (
    ApiBackend,
    AuthScheme,
    ProviderConfig,
    StreamEvent,
    ToolCall,
)

__all__ = [
    "ApiBackend",
    "AuthScheme",
    "ProviderConfig",
    "StreamEvent",
    "ToolCall",
]
