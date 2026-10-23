# [INT-10] 2026-08-09: Moved from cli/auth.py → api/client_factory.py.
# LLM client elastic assembly (breaker + retry + 401 refresh wiring) belongs in
# the api/ package (LLM Engine), not in CLI auth. The OAuth refresh callback
# stays in cli/auth.py (credential concern) and is passed in as a parameter.
"""ChatClient factory — wires circuit breaker + retry + 401 refresh."""

from __future__ import annotations

from typing import Awaitable, Callable

from code_agent.api.client import ChatClient
from code_agent.api.schemas import ProviderConfig
from code_agent.helpers.resilience import (
    BreakerConfig,
    CircuitBreaker,
    RefreshGate,
    RetryConfig,
    RetryPolicy,
)


def build_resilient_client(
    config: ProviderConfig,
    refresh_cb: Callable[[], Awaitable[str | None]] | None = None,
) -> ChatClient:
    """Build a ChatClient with circuit breaker + retry + 401 refresh.

    Transient 5xx/429/408 are retried with backoff (max 2), and a 401
    triggers a best-effort OAuth refresh + single retry. The circuit breaker
    fail-fasts when the error rate spikes.

    Args:
        config: Provider connection config.
        refresh_cb: Async OAuth token-refresh callback (None when OAuth unused,
            e.g. plain API-key auth — the 401 then surfaces as-is).
    """
    return ChatClient(
        config,
        breaker=CircuitBreaker(BreakerConfig.client()),
        retry=RetryPolicy.client_storage(),
        retry_cfg=RetryConfig(max_retries=2),
        refresh_cb=refresh_cb,
        refresh_gate=RefreshGate(),
    )
