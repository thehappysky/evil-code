# [OBS-01] 2026-08-09: Moved from code_agent/helpers/tracer.py → telemetry/tracer.py.
# TraceWriter is observability infra, not a pure helper. Co-located with the rest
# of the telemetry/ package (TelemetryLogger, UsageTracker).
"""Structured JSONL trace logger — audit trail for agent behavior.

Writes one JSON line per event to ~/.evil-code/traces/<session-id>.jsonl.

Event types:
    session_start  — new conversation begins
    auth_resolve   — which auth level was used + provider config (key masked)
    api_request    — HTTP request sent to LLM (url, headers, body)
    api_response   — HTTP response status
    sse_raw        — raw SSE JSON chunk from the wire
    turn_start     — reasoning turn N begins
    turn_end       — reasoning turn ends (text response or tool calls)
    tool_call      — tool invoked (name, arguments)
    tool_result    — tool returned (output preview, is_error, truncated)
    error          — any error event

Usage:
    from code_agent.telemetry.tracer import TraceWriter

    trace = TraceWriter()
    trace.event("tool_call", tool="file_read", args={"path": "..."})
    # -> writes one JSON line to the trace file
    trace.close()

All events include: timestamp (ISO 8601), session_id, sequence number.

Design: zero project-internal dependencies, stdlib only.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

_TRACE_DIR = Path.home() / ".evil-code" / "traces"


class TraceWriter:
    """Writes structured JSONL traces to a session file."""

    def __init__(self, session_id: str | None = None):
        self._session_id = session_id or uuid.uuid4().hex[:12]
        self._seq: int = 0
        self._started_at: float = time.time()
        self._file = None
        self._opened = False

    # ── context manager ──────────────────────────────────────────────────────

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def open(self) -> None:
        if self._opened:
            return
        _TRACE_DIR.mkdir(parents=True, exist_ok=True)
        fname = f"{self._session_id}.jsonl"
        self._file = open(str(_TRACE_DIR / fname), "w", encoding="utf-8")
        self._opened = True
        self.event("session_start", uptime=0.0)

    def close(self) -> None:
        if not self._opened:
            return
        self.event("session_end", uptime=round(time.time() - self._started_at, 3))
        if self._file:
            self._file.close()
            self._file = None
        self._opened = False

    @property
    def session_id(self) -> str:
        return self._session_id

    # ── core ─────────────────────────────────────────────────────────────────

    def event(self, kind: str, **payload: Any) -> None:
        """Write one trace event. Auto-opens if not yet opened.

        Args:
            kind: Event type string (session_start, api_request, tool_call, ...)
            **payload: Arbitrary JSON-serializable key-value pairs.
        """
        if not self._opened:
            self.open()

        self._seq += 1
        record: dict[str, Any] = {
            "seq": self._seq,
            "ts": time.time(),
            "uptime": round(time.time() - self._started_at, 4),
            "kind": kind,
        }
        record.update(self._sanitize(payload))
        assert self._file is not None
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._file.flush()

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _sanitize(obj: Any) -> Any:
        """Recursively mask api_key fields and truncate large strings."""
        if isinstance(obj, dict):
            return {
                k: ("***" if _is_sensitive(k) else TraceWriter._sanitize(v))
                for k, v in obj.items()
            }
        if isinstance(obj, str) and len(obj) > 2000:
            return obj[:2000] + f"... [{len(obj)} total chars]"
        if isinstance(obj, (list, tuple)):
            return [TraceWriter._sanitize(v) for v in obj]
        return obj


def _is_sensitive(key: str) -> bool:
    return key in ("api_key", "apiKey", "accessToken", "refreshToken", "x-api-key",
                   "Authorization", "authorization", "auth_token", "api-key",
                   "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
