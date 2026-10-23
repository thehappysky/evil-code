# [Phase 4] 2026-07-29: Telemetry — structured JSON event logging
"""Structured JSON event logging.

References:
    - design-docs/Python-Agent-MVP-Phase4-详细设计.md §7 telemetry/logger.py
    - Rust xai-grok-telemetry unified_log

Output: ~/.code-agent/logs/unified.jsonl (JSON lines format)
Each line is a self-contained JSON object.

Privacy guarantees:
    - No prompts, code content, file paths, or shell commands are logged
    - Session IDs and tool names are logged (needed for debugging)
    - Opt-in content logging via CODE_AGENT_LOG_CONTENT=1 env var
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_LOG_DIR = Path.home() / ".code-agent" / "logs"


class TelemetryLogger:
    """Structured JSON event logging.

    Event types logged:
    - session_create, session_resume, session_end
    - turn_start, turn_complete
    - tool_call, tool_result
    - compaction
    - error

    Usage::

        tl = TelemetryLogger()
        tl.set_session("abc123")
        tl.log("tool_call", tool="file_read", duration_ms=45)
    """

    def __init__(self, log_dir: Path | None = None):
        self._log_dir = log_dir or DEFAULT_LOG_DIR
        self._session_id: str = ""
        self._log_path: Path | None = None
        self._content_logging = os.environ.get("CODE_AGENT_LOG_CONTENT", "0") == "1"

    def set_session(self, session_id: str) -> None:
        """Set the active session ID and create a new log file.

        Args:
            session_id: Session identifier.
        """
        self._session_id = session_id
        self._log_dir.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._log_path = self._log_dir / f"{date_str}-{session_id}.jsonl"

    def log(self, event: str, **kwargs) -> None:
        """Log a structured event.

        Auto-adds timestamp, session_id, and event type.
        Privacy-safe: strips content unless CODE_AGENT_LOG_CONTENT=1.

        Args:
            event: Event type string (e.g., "turn_complete", "tool_call").
            **kwargs: Event-specific data.
        """
        if not self._log_path:
            return

        entry: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": self._session_id,
            "event": event,
        }

        # Sanitize: remove sensitive data unless content logging enabled
        if not self._content_logging:
            kwargs = _sanitize_kwargs(kwargs)

        entry.update(kwargs)

        try:
            line = json.dumps(entry, ensure_ascii=False)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as exc:
            logger.warning("Failed to write telemetry event: %s", exc)


class UsageTracker:
    """Token usage aggregation.

    Dimensions: session, model, date.
    Stores data in-memory and persists on session end.

    Usage::

        tracker = UsageTracker()
        tracker.record_usage("abc123", "deepseek-v4-pro", 7210, 1893)
        stats = tracker.get_session_stats("abc123")
    """

    def __init__(self):
        self._records: list[dict] = []

    def record_usage(
        self,
        session_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Record a usage entry.

        Args:
            session_id: Session identifier.
            model: Model ID.
            input_tokens: Number of input/prompt tokens.
            output_tokens: Number of output/completion tokens.
        """
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        self._records.append(record)

    def get_session_stats(self, session_id: str) -> dict:
        """Get aggregated usage stats for a session.

        Args:
            session_id: Session identifier.

        Returns:
            Dict with total_input_tokens, total_output_tokens, turns, tools_called.
        """
        session_records = [r for r in self._records if r["session_id"] == session_id]

        total_input = sum(r["input_tokens"] for r in session_records)
        total_output = sum(r["output_tokens"] for r in session_records)

        return {
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "turns": len(session_records),
        }

    def get_model_stats(self, model: str) -> dict:
        """Get aggregate stats per model across all sessions.

        Args:
            model: Model ID.

        Returns:
            Dict with total tokens, sessions, turns.
        """
        model_records = [r for r in self._records if r["model"] == model]
        sessions = set(r["session_id"] for r in model_records)

        return {
            "total_input_tokens": sum(r["input_tokens"] for r in model_records),
            "total_output_tokens": sum(r["output_tokens"] for r in model_records),
            "sessions": len(sessions),
            "turns": len(model_records),
        }

    def all_records(self) -> list[dict]:
        """Return all usage records."""
        return list(self._records)


# ── Internal helpers ────────────────────────────────────────────────────────

_SENSITIVE_KEYS = frozenset({
    "prompt", "content", "text", "file_path", "code",
    "arguments", "args", "command", "url", "path",
})


def _sanitize_kwargs(kwargs: dict) -> dict:
    """Remove sensitive data from log kwargs.

    Args:
        kwargs: Raw keyword arguments.

    Returns:
        Sanitized dict with sensitive keys replaced by '[redacted]'.
    """
    sanitized: dict = {}
    for key, value in kwargs.items():
        if key.lower() in _SENSITIVE_KEYS:
            sanitized[key] = "[redacted]"
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_kwargs(value)
        else:
            sanitized[key] = value
    return sanitized
