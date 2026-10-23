"""LLM HTTP client - multi-provider via httpx direct + backend dispatch.

Logic reference: Rust version xai-grok-sampler crate's SamplingClient::post()
(client.rs:550) — construct request, send HTTP, parse SSE stream via the
backend's transform function.

Design decisions:
- Multi-provider: dispatches to ChatCompletions/Messages/Responses backend
  based on ProviderConfig.api_backend
- httpx direct: no SDK dependency, full control over headers + SSE parsing
- Auth is pre-resolved: ProviderConfig.api_key is already set by cli/main.py
  (4-level chain: CLI > env > settings.json > OAuth). ChatClient doesn't
  read token files or refresh — that's the caller's job.
- Streaming: chat_stream() is an async generator yielding StreamEvent
- Dependencies: httpx, code_agent.api.backends, code_agent.api.sse,
  code_agent.api.schemas, code_agent.core.exceptions
- Does NOT depend on: openai SDK, Session, ToolRegistry, CLI/TUI, auth/
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import replace

import httpx

from code_agent.api.backends import get_backend
from code_agent.helpers.resilience import (
    BreakerOpen,
    Outcome,
    RefreshGate,
    RetryConfig,
    RetryPolicy,
)
from code_agent.api.schemas import ProviderConfig, StreamEvent
from code_agent.api.sse import iter_sse_events
from code_agent.core.exceptions import AuthenticationError
from code_agent.telemetry.tracer import TraceWriter

logger = logging.getLogger(__name__)


class ChatClient:
    """LLM conversation client. Multi-provider via httpx + backend dispatch.

    Usage:
        config = ProviderConfig(
            api_backend=ApiBackend.MESSAGES,
            base_url="https://api.deepseek.com/anthropic",
            api_key="sk-...",
            auth_scheme=AuthScheme.BEARER,
            model="deepseek-v4-pro",
        )
        client = ChatClient(config)
        async for event in client.chat_stream(messages, tools):
            if event.kind == "text_delta":
                print(event.text, end="")
    """

    def __init__(
        self,
        config: ProviderConfig,
        *,
        breaker=None,
        retry: "RetryPolicy | None" = None,
        retry_cfg: "RetryConfig | None" = None,
        refresh_cb=None,
        refresh_gate=None,
    ):
        """Initialize with a fully-resolved ProviderConfig.

        The config must already have api_key set — ChatClient does not
        perform auth resolution (that's done in cli/main.py).

        Resilience (Phase 3, optional — all default to None → legacy
        behavior, so existing callers/tests are unchanged):
            breaker: CircuitBreaker instance (fail-fast gate).
            retry: RetryPolicy (status classification).
            retry_cfg: RetryConfig; when present, transient failures are
                retried with backoff and 401 attempts an OAuth refresh.
            refresh_cb: async () -> str | None returning a fresh token.
            refresh_gate: RefreshGate deduplicating concurrent refreshes.
        """
        self._config = config
        self._http_client = httpx.AsyncClient(
            follow_redirects=False,
            http2=False,
        )
        self._breaker = breaker
        self._retry = retry
        self._retry_cfg = retry_cfg
        self._refresh_cb = refresh_cb
        self._refresh_gate = refresh_gate

    @property
    def model(self) -> str:
        """The active model id (used for usage ledger accounting)."""
        return self._config.model

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        trace: TraceWriter | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Send messages to LLM and stream back events.

        Args:
            messages: OpenAI-format message list (from Session.to_api_messages()).
            tools: Optional tool definitions in OpenAI format.
            max_tokens: Override config.max_tokens for this call.
            temperature: Override config.temperature for this call.
            trace: Optional TraceWriter for structured audit logging.

        Yields:
            StreamEvent instances (text_delta, tool_call_delta,
            thinking_delta, turn_end, error).

        Raises:
            AuthenticationError: If API key is missing or API returns 401.
        """
        if not self._config.api_key:
            raise AuthenticationError(
                "No API key available. Set --api-key, ANTHROPIC_API_KEY "
                "env var, or configure ~/.claude/settings.json"
            )

        # Apply per-request overrides
        config = self._config
        if max_tokens is not None or temperature is not None:
            config = replace(
                config,
                max_tokens=max_tokens or config.max_tokens,
                temperature=(
                    temperature if temperature is not None else config.temperature
                ),
            )

        backend = get_backend(config.api_backend)

        # Circuit-breaker fail-fast gate (only when resilience is enabled).
        if self._breaker is not None:
            breaker_error = self._breaker_error()
            if breaker_error is not None:
                yield breaker_error
                return

        max_retries = self._retry_cfg.max_retries if self._retry_cfg else 0
        attempt = 0

        while True:
            req = backend.build_request(messages, tools, config)

            if trace:
                trace.event("api_request",
                            url=req.url,
                            model=config.model,
                            backend=config.api_backend.value,
                            message_count=len(messages),
                            tool_count=len(tools) if tools else 0)

            try:
                async with self._http_client.stream(
                    "POST",
                    req.url,
                    headers=req.headers,
                    json=req.body,
                    timeout=httpx.Timeout(300.0, connect=10.0),
                ) as response:
                    status = response.status_code

                    # 401: attempt an OAuth refresh + single retry.
                    if status == 401:
                        self._record_failure()
                        if (
                            attempt < max_retries
                            and self._refresh_gate is not None
                            and self._refresh_cb is not None
                        ):
                            new_key = await self._refresh_gate.run_once(
                                self._refresh_cb,
                            )
                            if new_key:
                                config = replace(config, api_key=new_key)
                                attempt += 1
                                if not self._continue_after_retry():
                                    return
                                continue
                        body = await response.aread()
                        raise AuthenticationError(
                            f"API authentication failed (401): "
                            f"{body.decode('utf-8', errors='replace')[:500]}"
                        )

                    # Retryable transient failure (5xx / 429 / 408).
                    if self._is_retryable(status):
                        self._record_failure()
                        if attempt < max_retries:
                            await asyncio.sleep(
                                self._retry_cfg.delay_for(
                                    attempt, self._retry_after_from(response),
                                )
                            )
                            attempt += 1
                            if not self._continue_after_retry():
                                return
                            continue
                        body = await response.aread()
                        yield StreamEvent(
                            kind="error",
                            text=(
                                f"HTTP {status}: "
                                f"{body.decode('utf-8', errors='replace')[:500]}"
                            ),
                        )
                        return

                    if status != 200:
                        self._record_failure()
                        body = await response.aread()
                        yield StreamEvent(
                            kind="error",
                            text=(
                                f"HTTP {status}: "
                                f"{body.decode('utf-8', errors='replace')[:500]}"
                            ),
                        )
                        return

                    # Success (200): stream the response.
                    self._record_success()
                    sse_events = iter_sse_events(response)
                    async for event in backend.parse_stream(sse_events):
                        if trace:
                            trace.event("sse_event", kind=event.kind,
                                        text=(event.text or "")[:200],
                                        tool_name=(
                                            event.tool_call.name
                                            if event.tool_call and event.tool_call.name
                                            else None
                                        ))
                        yield event
                    return

            except AuthenticationError:
                raise
            except httpx.ConnectError as exc:
                self._record_failure()
                if self._retry_cfg is None or attempt >= max_retries:
                    yield StreamEvent(
                        kind="error", text=f"Connection failed: {exc}",
                    )
                    return
                await asyncio.sleep(self._retry_cfg.delay_for(attempt))
                attempt += 1
                if not self._continue_after_retry():
                    return
            except httpx.TimeoutException as exc:
                self._record_failure()
                if self._retry_cfg is None or attempt >= max_retries:
                    yield StreamEvent(
                        kind="error", text=f"Request timed out: {exc}",
                    )
                    return
                await asyncio.sleep(self._retry_cfg.delay_for(attempt))
                attempt += 1
                if not self._continue_after_retry():
                    return
            except Exception as exc:
                error_str = str(exc).lower()
                is_auth = (
                    "401" in error_str
                    or "unauthorized" in error_str
                    or "invalid api key" in error_str
                    or "incorrect api key" in error_str
                )
                if is_auth:
                    raise AuthenticationError(
                        f"API authentication failed: {exc}"
                    ) from exc
                yield StreamEvent(kind="error", text=str(exc))
                return

    # ── Resilience helpers ─────────────────────────────────────────────────

    def _record_success(self) -> None:
        if self._breaker is not None:
            self._breaker.record(Outcome.SUCCESS)

    def _record_failure(self) -> None:
        if self._breaker is not None:
            self._breaker.record(Outcome.FAILURE)

    def _is_retryable(self, status: int) -> bool:
        if self._retry_cfg is None or self._retry is None:
            return False
        return self._retry.should_retry(status)

    def _retry_after_from(self, response: httpx.Response) -> float | None:
        """Read a server-provided ``retry-after`` header (seconds)."""
        ra = response.headers.get("retry-after")
        if ra:
            try:
                return max(0.0, float(ra))
            except ValueError:
                pass
        return None

    def _continue_after_retry(self) -> bool:
        """Backoff between retries and re-check the breaker.

        Returns False when the breaker has opened mid-retry (caller stops).
        """
        if self._breaker is not None:
            breaker_error = self._breaker_error()
            if breaker_error is not None:
                return False
        return True

    def _breaker_error(self) -> "StreamEvent | None":
        """Return an error event if the circuit breaker is open."""
        try:
            self._breaker.check()
            return None
        except BreakerOpen as exc:
            logger.warning("Circuit breaker open: %s", exc)
            return StreamEvent(
                kind="error",
                text=f"Circuit breaker open: retry in {exc.retry_after:.0f}s",
            )

    async def close(self) -> None:
        """Clean up HTTP resources."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def chat(
        self,
        messages: list[dict],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Convenience method: send messages and collect full text response.

        Collects all text_delta events from chat_stream() into a single
        string. Used by compaction summarizer and other non-streaming
        LLM calls.

        Args:
            messages: OpenAI-format message list.
            max_tokens: Override config.max_tokens.
            temperature: Override config.temperature.

        Returns:
            Full text response from the model.

        Raises:
            AuthenticationError: If API key is missing or 401.
        """
        parts: list[str] = []
        async for event in self.chat_stream(
            messages, tools=None, max_tokens=max_tokens,
            temperature=temperature,
        ):
            if event.kind == "text_delta" and event.text:
                parts.append(event.text)
            elif event.kind == "error":
                raise RuntimeError(f"LLM error during chat: {event.text}")
        return "".join(parts)
