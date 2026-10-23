"""Backend factory — maps ApiBackend enum to Backend instances.

Backends are stateless (pure build_request + parse_stream logic),
so instances can be singletons.

Usage:
    from code_agent.api.backends import get_backend
    from code_agent.api.schemas import ApiBackend

    backend = get_backend(ApiBackend.MESSAGES)
    req = backend.build_request(messages, tools, config)
"""

from __future__ import annotations

from code_agent.api.backends.base import Backend, BackendRequest
from code_agent.api.backends.chat_completions import ChatCompletionsBackend
from code_agent.api.backends.messages import MessagesBackend
from code_agent.api.backends.responses import ResponsesBackend
from code_agent.api.schemas import ApiBackend

# Singleton instances — backends are stateless
_BACKENDS: dict[ApiBackend, Backend] = {
    ApiBackend.CHAT_COMPLETIONS: ChatCompletionsBackend(),
    ApiBackend.MESSAGES: MessagesBackend(),
    ApiBackend.RESPONSES: ResponsesBackend(),
}


def get_backend(api_backend: ApiBackend) -> Backend:
    """Get the singleton Backend instance for the given protocol.

    Args:
        api_backend: The API backend protocol enum value.

    Returns:
        Backend instance implementing build_request + parse_stream.

    Raises:
        KeyError: If no backend is registered for the given protocol.
    """
    return _BACKENDS[api_backend]


__all__ = [
    "Backend",
    "BackendRequest",
    "get_backend",
]
