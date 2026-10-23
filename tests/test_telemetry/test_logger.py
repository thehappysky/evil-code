# [Phase 4] 2026-07-29: Tests for telemetry/logger.py
"""Tests for TelemetryLogger and UsageTracker."""

import json
import tempfile
from pathlib import Path

import pytest

from code_agent.telemetry.logger import TelemetryLogger, UsageTracker


class TestTelemetryLogger:
    """Test TelemetryLogger."""

    def test_log_writes_json_line(self):
        """log() writes a JSON line to the log file."""
        with tempfile.TemporaryDirectory() as tmp:
            logger = TelemetryLogger(log_dir=Path(tmp))
            logger.set_session("test-session-123")
            logger.log("turn_start", turn=1, tool_count=5)

            log_path = logger._log_path
            assert log_path is not None
            assert log_path.exists()

            lines = log_path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 1

            entry = json.loads(lines[0])
            assert entry["event"] == "turn_start"
            assert entry["session_id"] == "test-session-123"
            assert entry["turn"] == 1
            assert entry["tool_count"] == 5
            assert "ts" in entry

    def test_log_redacts_sensitive_data(self):
        """Content logging is disabled by default (redacts sensitive fields)."""
        with tempfile.TemporaryDirectory() as tmp:
            logger = TelemetryLogger(log_dir=Path(tmp))
            logger.set_session("session-001")
            logger.log("tool_call", tool="file_read", file_path="/secret/path")

            log_path = logger._log_path
            assert log_path is not None

            lines = log_path.read_text(encoding="utf-8").strip().split("\n")
            entry = json.loads(lines[0])
            # file_path should be redacted
            assert entry["file_path"] == "[redacted]"

    def test_log_without_session_does_nothing(self):
        """log() without set_session doesn't write."""
        logger = TelemetryLogger(log_dir=Path("/tmp/nonexistent"))
        # No set_session called
        logger.log("some_event")  # Should not raise

    def test_log_multiple_events(self):
        """Multiple log events are written as separate lines."""
        with tempfile.TemporaryDirectory() as tmp:
            logger = TelemetryLogger(log_dir=Path(tmp))
            logger.set_session("session-multi")
            logger.log("event_a", a=1)
            logger.log("event_b", b=2)

            lines = logger._log_path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 2


class TestUsageTracker:
    """Test UsageTracker."""

    def test_record_and_get_stats(self):
        """record_usage and get_session_stats work correctly."""
        tracker = UsageTracker()
        tracker.record_usage("s1", "deepseek-v4-pro", 1000, 500)
        tracker.record_usage("s1", "deepseek-v4-pro", 2000, 1000)

        stats = tracker.get_session_stats("s1")
        assert stats["total_input_tokens"] == 3000
        assert stats["total_output_tokens"] == 1500
        assert stats["total_tokens"] == 4500
        assert stats["turns"] == 2

    def test_multiple_sessions(self):
        """Stats are per-session."""
        tracker = UsageTracker()
        tracker.record_usage("s1", "model-a", 100, 50)
        tracker.record_usage("s2", "model-a", 200, 100)

        s1 = tracker.get_session_stats("s1")
        s2 = tracker.get_session_stats("s2")

        assert s1["total_tokens"] == 150
        assert s2["total_tokens"] == 300

    def test_get_model_stats(self):
        """Per-model aggregation works."""
        tracker = UsageTracker()
        tracker.record_usage("s1", "model-a", 100, 50)
        tracker.record_usage("s2", "model-a", 200, 100)
        tracker.record_usage("s3", "model-b", 300, 150)

        model_a = tracker.get_model_stats("model-a")
        assert model_a["sessions"] == 2
        assert model_a["total_input_tokens"] == 300

        model_b = tracker.get_model_stats("model-b")
        assert model_b["sessions"] == 1

    def test_unknown_session_returns_zeros(self):
        """Unknown session returns zeros."""
        tracker = UsageTracker()
        stats = tracker.get_session_stats("nonexistent")
        assert stats["total_tokens"] == 0
