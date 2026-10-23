# [Phase 5 / Phase 3] 2026-08-04: Resilience layer — circuit breaker + retry + 401 recovery
# [ORCH-01] 2026-08-09: Moved from code_agent/api/resilience.py → helpers/resilience.py.
# General-purpose infra (used by api/client.py + compaction/summarizer.py), so it
# belongs in the shared helpers/ kernel, not in the api/ package.
"""Resilience primitives for the LLM HTTP layer.

References:
    - design-docs/Python-Agent-补足方案-设计.md §Phase 3
    - Rust xai-circuit-breaker (breaker.rs, window.rs, retry_policy.rs)
    - Rust xai-grok-auth (retry_middleware.rs, 401 OnceCell dedup)

Contents:
- ``CircuitBreaker``: three-state (CLOSED/OPEN/HALF_OPEN) breaker with a
  sliding-window error-rate trip and half-open probe recovery.
- ``RetryPolicy``: classify HTTP status codes into RETRYABLE / AUTH_REFRESH /
  TERMINAL, with server and client preset tables.
- ``RetryConfig``: exponential backoff + jitter + ``retry-after`` respect.
- ``RefreshGate``: deduplicates concurrent token-refresh calls (only one
  refresh in flight; everyone awaits the same future).

The CircuitBreaker is intentionally synchronous (``check``/``record`` have no
``await``) so it can be called anywhere in a streaming response loop. Only
``RefreshGate`` is async.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from random import random
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


# ─── Circuit breaker ───────────────────────────────────────────────────────────


class BreakerState(Enum):
    CLOSED = "closed"       # normal operation, error rate sampled
    OPEN = "open"           # failing fast; no requests allowed
    HALF_OPEN = "half_open"  # probing to see if the dependency recovered


class Outcome(Enum):
    SUCCESS = "success"
    FAILURE = "failure"


class BreakerOpen(Exception):
    """Raised by ``CircuitBreaker.check()`` when the breaker is open.

    Attributes:
        retry_after: Seconds until the breaker may probe again.
    """

    def __init__(self, retry_after: float):
        super().__init__(f"circuit breaker open; retry in {retry_after:.1f}s")
        self.retry_after = retry_after


@dataclass
class BreakerConfig:
    """Configuration for a CircuitBreaker.

    Attributes:
        window_duration: Sliding window length in seconds.
        min_samples: Minimum samples before the error rate is trusted.
        error_rate_threshold: Fraction of failures (0..1) that trips the breaker.
        open_duration: Seconds the breaker stays OPEN before probing.
        half_open_max_probes: Concurrent probes allowed in HALF_OPEN.
        failure_codes: HTTP statuses counted as failures.
        enabled: If False, check()/record() are no-ops.
    """

    window_duration: float = 60.0
    min_samples: int = 10
    error_rate_threshold: float = 0.5
    open_duration: float = 30.0
    half_open_max_probes: int = 1
    failure_codes: frozenset = frozenset({429, 500, 502, 503, 504})
    enabled: bool = True

    def is_failure_status(self, status: int) -> bool:
        return status in self.failure_codes

    @classmethod
    def client(cls) -> "BreakerConfig":
        """Preset for LLM client calls (retries on transient server errors)."""
        return cls(
            window_duration=60.0,
            min_samples=5,
            error_rate_threshold=0.5,
            open_duration=30.0,
            half_open_max_probes=1,
            failure_codes=frozenset({429, 500, 502, 503, 504}),
        )

    @classmethod
    def server(cls) -> "BreakerConfig":
        """Preset for outbound provider calls (more conservative)."""
        return cls(
            window_duration=120.0,
            min_samples=10,
            error_rate_threshold=0.3,
            open_duration=60.0,
            half_open_max_probes=1,
            failure_codes=frozenset({500, 502, 503, 504}),
        )


class SlidingWindow:
    """Fixed-window error-rate sampler (deque of (timestamp, is_failure))."""

    def __init__(self, window_duration: float, clock: Callable[[], float] = time.monotonic):
        self._window = window_duration
        self._clock = clock
        self._entries: deque[tuple[float, bool]] = deque()
        self._failures = 0
        self._max_entries = 10_000

    def push(self, now: float, is_failure: bool) -> None:
        self._entries.append((now, is_failure))
        if is_failure:
            self._failures += 1
        if len(self._entries) > self._max_entries:
            _, was_failure = self._entries.popleft()
            if was_failure:
                self._failures -= 1

    def evict(self, now: float) -> None:
        cutoff = now - self._window
        while self._entries and self._entries[0][0] < cutoff:
            _, was_failure = self._entries.popleft()
            if was_failure:
                self._failures -= 1

    def error_rate(self) -> float:
        if not self._entries:
            return 0.0
        return self._failures / len(self._entries)

    def sample_count(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()
        self._failures = 0


class CircuitBreaker:
    """Three-state circuit breaker.

    Usage (synchronous, safe to call from an async stream loop)::

        breaker = CircuitBreaker(BreakerConfig.client())
        try:
            breaker.check()          # raises BreakerOpen when OPEN
            ...
            breaker.record(Outcome.SUCCESS)
        except Exception:
            breaker.record(Outcome.FAILURE)
            raise

    ``check()`` in OPEN state raises ``BreakerOpen``; once ``open_duration``
    elapses it transitions to HALF_OPEN and admits up to
    ``half_open_max_probes`` concurrent probes. A probe success closes the
    breaker; a probe failure re-opens it.
    """

    def __init__(
        self,
        config: BreakerConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._config = config
        self._clock = clock
        self._state = BreakerState.CLOSED
        self._window = SlidingWindow(config.window_duration, clock)
        self._opened_at: float | None = None
        self._probe_slots = 0
        self._lock = threading.Lock()

    # ── State transitions ────────────────────────────────────────────────

    def check(self) -> None:
        """Fail-fast gate. Raises BreakerOpen when requests must be refused."""
        if not self._config.enabled:
            return
        with self._lock:
            now = self._clock()
            if self._state is BreakerState.OPEN:
                assert self._opened_at is not None
                elapsed = now - self._opened_at
                if elapsed >= self._config.open_duration:
                    self._state = BreakerState.HALF_OPEN
                    self._probe_slots = self._config.half_open_max_probes
                else:
                    raise BreakerOpen(self._config.open_duration - elapsed)
            if self._state is BreakerState.HALF_OPEN:
                if self._probe_slots <= 0:
                    raise BreakerOpen(
                        self._config.open_duration - (now - (self._opened_at or now)),
                    )
                self._probe_slots -= 1

    def record(self, outcome: Outcome) -> None:
        """Record a request outcome and update the state machine."""
        if not self._config.enabled:
            return
        with self._lock:
            now = self._clock()
            self._window.evict(now)
            is_failure = outcome is Outcome.FAILURE

            if self._state is BreakerState.HALF_OPEN:
                if is_failure:
                    self._state = BreakerState.OPEN
                    self._opened_at = now
                else:
                    self._state = BreakerState.CLOSED
                    self._window.clear()
                return

            if self._state is BreakerState.OPEN:
                # Sample anyway so recovery decisions have data.
                self._window.push(now, is_failure)
                return

            # CLOSED
            self._window.push(now, is_failure)
            if (
                self._window.sample_count() >= self._config.min_samples
                and self._window.error_rate() >= self._config.error_rate_threshold
            ):
                logger.warning(
                    "Circuit breaker OPEN: error rate %.0f%% over %d samples",
                    self._window.error_rate() * 100,
                    self._window.sample_count(),
                )
                self._state = BreakerState.OPEN
                self._opened_at = now

    # ── Queries / helpers ─────────────────────────────────────────────────

    def is_open(self) -> bool:
        return self._state is BreakerState.OPEN

    @property
    def state(self) -> BreakerState:
        return self._state

    def error_rate(self) -> float:
        with self._lock:
            return self._window.error_rate()

    def force_half_open(self) -> None:
        """Test hook: force HALF_OPEN with one probe slot."""
        with self._lock:
            self._state = BreakerState.HALF_OPEN
            self._probe_slots = self._config.half_open_max_probes


# ─── Retry policy ──────────────────────────────────────────────────────────────


class Disposition(Enum):
    RETRYABLE = "retryable"
    AUTH_REFRESH = "auth_refresh"
    TERMINAL = "terminal"


class RetryPolicy:
    """Classifies HTTP statuses to decide whether a call is retryable.

    Tables mirror ``retry_policy.rs``:
    - server: retry 5xx + 429, auth-refresh on 401, terminal on 4xx.
    - client_storage: same but also retries 408 (request timeout).
    """

    def __init__(self, *, retry_codes: frozenset, refresh_code: int = 401):
        self._retry_codes = retry_codes
        self._refresh_code = refresh_code

    def classify(self, status: int) -> Disposition | None:
        if status == self._refresh_code:
            return Disposition.AUTH_REFRESH
        if status in self._retry_codes:
            return Disposition.RETRYABLE
        if 400 <= status < 600:
            return Disposition.TERMINAL
        return None

    def should_retry(self, status: int) -> bool:
        return self.classify(status) is Disposition.RETRYABLE

    @classmethod
    def server(cls) -> "RetryPolicy":
        return cls(retry_codes=frozenset({429, 500, 502, 503, 504}))

    @classmethod
    def client_storage(cls) -> "RetryPolicy":
        return cls(retry_codes=frozenset({408, 429, 500, 502, 503, 504}))


@dataclass
class RetryConfig:
    """Exponential backoff with jitter.

    Attributes:
        initial_delay: First retry delay in seconds.
        max_delay: Cap on a single delay.
        max_retries: Maximum retry attempts after the initial call.
        multiplier: Exponential growth factor.
        jitter_factor: Random ±factor applied to each delay.
        respect_retry_after: Honor a ``retry-after`` header when present.
        max_retry_after: Cap on honoring ``retry-after``.
    """

    initial_delay: float = 0.5
    max_delay: float = 30.0
    max_retries: int = 5
    multiplier: float = 2.0
    jitter_factor: float = 0.5
    respect_retry_after: bool = True
    max_retry_after: float = 60.0

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        """Compute the backoff delay for ``attempt`` (0-based).

        ``attempt`` is the number of retries already performed (0 for the
        first retry). Returns at least ``max(retry_after, delay)`` when a
        server-provided retry-after is present.
        """
        base = min(self.initial_delay * (self.multiplier ** attempt), self.max_delay)
        if self.jitter_factor > 0:
            base = base * (1.0 + (random() * 2 - 1) * self.jitter_factor)
            base = max(0.0, base)
        base = min(base, self.max_delay)
        if retry_after is not None and self.respect_retry_after:
            base = max(base, min(retry_after, self.max_retry_after))
        return base


# ─── 401 recovery (concurrent-refresh dedup) ───────────────────────────────────


class RefreshGate:
    """Deduplicates concurrent token refreshes.

    Only one refresh runs at a time; concurrent callers ``await`` the same
    in-flight future (mirrors Rust's OnceCell-deduped 401 recovery).
    """

    def __init__(self) -> None:
        self._in_flight: asyncio.Future | None = None
        self._lock = asyncio.Lock()

    async def run_once(
        self,
        refresh_cb: Callable[[], Awaitable[str | None]],
    ) -> str | None:
        """Run ``refresh_cb`` once; concurrent callers share the result.

        Returns the new access token (or None if refresh failed/absent).

        Args:
            refresh_cb: Async callable returning a new token or None.

        Returns:
            The refreshed token string, or None.
        """
        async with self._lock:
            if self._in_flight is None or self._in_flight.done():
                self._in_flight = asyncio.ensure_future(self._do_refresh(refresh_cb))
            future = self._in_flight
        try:
            return await asyncio.shield(future)
        except Exception:
            logger.warning("Token refresh failed", exc_info=True)
            return None

    async def _do_refresh(
        self, refresh_cb: Callable[[], Awaitable[str | None]],
    ) -> str | None:
        try:
            return await refresh_cb()
        except Exception:
            logger.warning("Token refresh raised", exc_info=True)
            return None
