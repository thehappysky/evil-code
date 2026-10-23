# [Phase 5 / Phase 3] 2026-08-04: Tests for api/resilience.py + ChatClient wiring
"""Tests for the resilience layer (circuit breaker / retry / 401 recovery).

- CircuitBreaker state machine with an injectable fake clock
- SlidingWindow error-rate sampling
- RetryPolicy status classification
- RetryConfig backoff
- RefreshGate concurrent-refresh dedup
- ChatClient integration: 401 refresh+retry, 5xx backoff retry, breaker fail-fast
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from code_agent.api.client import ChatClient
from code_agent.helpers.resilience import (
    BreakerConfig,
    BreakerOpen,
    BreakerState,
    CircuitBreaker,
    Disposition,
    Outcome,
    RefreshGate,
    RetryConfig,
    RetryPolicy,
    SlidingWindow,
)
from code_agent.api.schemas import ApiBackend, AuthScheme, ProviderConfig


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _make_config(api_key: str = "sk-test") -> ProviderConfig:
    return ProviderConfig(
        api_backend=ApiBackend.CHAT_COMPLETIONS,
        base_url="https://api.test.com/v1",
        api_key=api_key,
        auth_scheme=AuthScheme.BEARER,
        model="test-model",
    )


def _sse_success(body: str = "ok") -> bytes:
    lines = [
        f'data: {{"choices":[{{"delta":{{"content":"{body}"}},"finish_reason":null}}]}}',
        "",
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}',
        "",
        "data: [DONE]",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


# ─── SlidingWindow ─────────────────────────────────────────────────────────────


class TestSlidingWindow:
    def test_error_rate(self):
        clock = FakeClock()
        w = SlidingWindow(60.0, clock)
        for i in range(3):
            w.push(clock(), True)
            clock.advance(1)
        for i in range(2):
            w.push(clock(), False)
            clock.advance(1)
        assert w.sample_count() == 5
        assert w.error_rate() == pytest.approx(0.6)

    def test_eviction_drops_old_entries(self):
        clock = FakeClock()
        w = SlidingWindow(10.0, clock)
        w.push(clock(), True)
        clock.advance(20.0)
        w.push(clock(), False)
        w.evict(clock())
        # The old failure is outside the 10s window and must be evicted.
        assert w.sample_count() == 1
        assert w.error_rate() == 0.0

    def test_clear(self):
        clock = FakeClock()
        w = SlidingWindow(10.0, clock)
        w.push(clock(), True)
        w.clear()
        assert w.sample_count() == 0
        assert w.error_rate() == 0.0


# ─── CircuitBreaker ────────────────────────────────────────────────────────────


class TestCircuitBreaker:
    def test_closed_until_threshold(self):
        clock = FakeClock()
        cb = CircuitBreaker(
            BreakerConfig(min_samples=4, error_rate_threshold=0.5, open_duration=10),
            clock=clock,
        )
        for _ in range(3):
            cb.check()  # no raise
            cb.record(Outcome.FAILURE)
            clock.advance(1)
        assert cb.state is BreakerState.CLOSED

        # 4th failure trips it.
        cb.record(Outcome.FAILURE)
        assert cb.state is BreakerState.OPEN

    def test_open_raises_breaker_open(self):
        clock = FakeClock()
        cb = CircuitBreaker(
            BreakerConfig(min_samples=1, error_rate_threshold=0.0, open_duration=10),
            clock=clock,
        )
        cb.record(Outcome.FAILURE)
        assert cb.state is BreakerState.OPEN
        with pytest.raises(BreakerOpen):
            cb.check()

    def test_half_open_probe_success_closes(self):
        clock = FakeClock()
        cb = CircuitBreaker(
            BreakerConfig(min_samples=1, error_rate_threshold=0.0, open_duration=10),
            clock=clock,
        )
        cb.record(Outcome.FAILURE)  # → OPEN
        clock.advance(10.0)
        cb.check()  # → HALF_OPEN, probe admitted
        assert cb.state is BreakerState.HALF_OPEN
        cb.record(Outcome.SUCCESS)  # probe success → CLOSED
        assert cb.state is BreakerState.CLOSED
        cb.check()  # no raise

    def test_half_open_probe_failure_reopens(self):
        clock = FakeClock()
        cb = CircuitBreaker(
            BreakerConfig(min_samples=1, error_rate_threshold=0.0, open_duration=10),
            clock=clock,
        )
        cb.record(Outcome.FAILURE)
        clock.advance(10.0)
        cb.check()  # → HALF_OPEN
        cb.record(Outcome.FAILURE)  # probe failure → OPEN again
        assert cb.state is BreakerState.OPEN
        with pytest.raises(BreakerOpen):
            cb.check()

    def test_half_open_probe_limit(self):
        clock = FakeClock()
        cb = CircuitBreaker(
            BreakerConfig(min_samples=1, error_rate_threshold=0.0,
                          open_duration=10, half_open_max_probes=1),
            clock=clock,
        )
        cb.record(Outcome.FAILURE)
        clock.advance(10.0)
        cb.check()  # consumes the single probe slot
        with pytest.raises(BreakerOpen):
            cb.check()  # no probe slot left

    def test_disabled_is_noop(self):
        cb = CircuitBreaker(BreakerConfig(enabled=False))
        cb.check()  # no raise
        cb.record(Outcome.FAILURE)
        cb.check()  # still no raise
        assert cb.state is BreakerState.CLOSED

    def test_force_half_open(self):
        cb = CircuitBreaker(BreakerConfig(min_samples=1, error_rate_threshold=0.0))
        cb.record(Outcome.FAILURE)
        cb.force_half_open()
        assert cb.state is BreakerState.HALF_OPEN


# ─── RetryPolicy / RetryConfig ────────────────────────────────────────────────


class TestRetryPolicy:
    def test_classify(self):
        p = RetryPolicy.server()
        assert p.classify(200) is None
        assert p.classify(401) is Disposition.AUTH_REFRESH
        assert p.classify(503) is Disposition.RETRYABLE
        assert p.classify(404) is Disposition.TERMINAL
        assert p.should_retry(500) is True
        assert p.should_retry(404) is False

    def test_client_storage_retries_408(self):
        p = RetryPolicy.client_storage()
        assert p.should_retry(408) is True
        assert RetryPolicy.server().should_retry(408) is False


class TestRetryConfig:
    def test_delay_is_exponential_and_capped(self):
        cfg = RetryConfig(initial_delay=1.0, multiplier=2.0, max_delay=30.0,
                          jitter_factor=0.0)
        assert cfg.delay_for(0) == 1.0
        assert cfg.delay_for(1) == 2.0
        assert cfg.delay_for(2) == 4.0
        # 1 * 2^5 = 32 → capped at 30
        assert cfg.delay_for(5) == 30.0

    def test_respects_retry_after(self):
        cfg = RetryConfig(initial_delay=1.0, max_delay=30.0, jitter_factor=0.0)
        assert cfg.delay_for(0, retry_after=5.0) == 5.0

    def test_jitter_bounds(self):
        cfg = RetryConfig(initial_delay=1.0, max_delay=30.0, jitter_factor=0.5)
        for _ in range(50):
            d = cfg.delay_for(0)
            assert 0.5 <= d <= 1.5


# ─── RefreshGate ───────────────────────────────────────────────────────────────


class TestRefreshGate:
    async def test_dedups_concurrent_refreshes(self):
        calls = 0

        async def _cb():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return "new-token"

        gate = RefreshGate()
        results = await asyncio.gather(
            gate.run_once(_cb),
            gate.run_once(_cb),
            gate.run_once(_cb),
        )
        assert calls == 1
        assert results == ["new-token", "new-token", "new-token"]

    async def test_sequential_calls_allow_new_refresh(self):
        calls = 0

        async def _cb():
            nonlocal calls
            calls += 1
            return f"tok-{calls}"

        gate = RefreshGate()
        first = await gate.run_once(_cb)
        second = await gate.run_once(_cb)
        assert calls == 2
        assert first == "tok-1"
        assert second == "tok-2"

    async def test_refresh_failure_returns_none(self):
        async def _cb():
            raise RuntimeError("boom")

        gate = RefreshGate()
        assert await gate.run_once(_cb) is None


# ─── ChatClient integration ────────────────────────────────────────────────────


class TestChatClientResilience:
    async def test_401_refresh_then_success(self, httpx_mock):
        httpx_mock.add_response(status_code=401, content=b'{"error":"expired"}')
        httpx_mock.add_response(
            status_code=200, content=_sse_success("Hello"),
            headers={"content-type": "text/event-stream"},
        )

        refreshed = []

        async def _refresh():
            refreshed.append(True)
            return "new-token"

        client = ChatClient(
            _make_config(),
            retry=RetryPolicy.client_storage(),
            retry_cfg=RetryConfig(max_retries=1),
            refresh_cb=_refresh,
            refresh_gate=RefreshGate(),
        )
        events = [e async for e in client.chat_stream([])]

        assert refreshed == [True]
        assert any(e.kind == "text_delta" for e in events)
        # The retried request used the refreshed key.
        assert client._config.api_key == "sk-test"  # original config untouched
        # The request header carried the new key on the second attempt.
        assert httpx_mock.get_requests()[-1].headers["Authorization"] == "Bearer new-token"

    async def test_401_without_refresh_raises(self, httpx_mock):
        httpx_mock.add_response(status_code=401, content=b'{"error":"expired"}')
        client = ChatClient(
            _make_config(),
            retry=RetryPolicy.client_storage(),
            retry_cfg=RetryConfig(max_retries=1),
            refresh_cb=None,
            refresh_gate=None,
        )
        from code_agent.core.exceptions import AuthenticationError

        with pytest.raises(AuthenticationError):
            async for _ in client.chat_stream([]):
                pass

    async def test_5xx_retried_then_success(self, httpx_mock):
        httpx_mock.add_response(status_code=503, content=b'busy')
        httpx_mock.add_response(
            status_code=200, content=_sse_success("ok"),
            headers={"content-type": "text/event-stream"},
        )
        client = ChatClient(
            _make_config(),
            retry=RetryPolicy.client_storage(),
            retry_cfg=RetryConfig(max_retries=2, initial_delay=0.01, jitter_factor=0.0),
        )
        events = [e async for e in client.chat_stream([])]
        assert any(e.kind == "text_delta" for e in events)
        assert len(httpx_mock.get_requests()) == 2

    async def test_5xx_exhausted_yields_error(self, httpx_mock):
        for _ in range(3):
            httpx_mock.add_response(status_code=503, content=b'busy')
        client = ChatClient(
            _make_config(),
            retry=RetryPolicy.client_storage(),
            retry_cfg=RetryConfig(max_retries=2, initial_delay=0.01, jitter_factor=0.0),
        )
        events = [e async for e in client.chat_stream([])]
        assert events and events[0].kind == "error"
        assert len(httpx_mock.get_requests()) == 3

    async def test_non_retryable_4xx_not_retried(self, httpx_mock):
        httpx_mock.add_response(status_code=400, content=b'bad')
        client = ChatClient(
            _make_config(),
            retry=RetryPolicy.client_storage(),
            retry_cfg=RetryConfig(max_retries=3, initial_delay=0.01),
        )
        events = [e async for e in client.chat_stream([])]
        assert events[0].kind == "error"
        assert len(httpx_mock.get_requests()) == 1

    async def test_breaker_open_fail_fast(self, httpx_mock):
        breaker = CircuitBreaker(
            BreakerConfig(min_samples=1, error_rate_threshold=0.0),
        )
        breaker.record(Outcome.FAILURE)
        assert breaker.is_open()

        client = ChatClient(_make_config(), breaker=breaker)
        events = [e async for e in client.chat_stream([])]
        assert events and events[0].kind == "error"
        assert "breaker" in events[0].text.lower()
        assert httpx_mock.get_requests() == []

    async def test_breaker_records_outcomes(self, httpx_mock):
        httpx_mock.add_response(
            status_code=200, content=_sse_success("ok"),
            headers={"content-type": "text/event-stream"},
        )
        breaker = CircuitBreaker(BreakerConfig.client())
        client = ChatClient(_make_config(), breaker=breaker)
        events = [e async for e in client.chat_stream([])]
        assert any(e.kind == "text_delta" for e in events)
        assert breaker.error_rate() == 0.0  # success recorded
