# [Phase 5] 2026-08-04: Tests for TelemetryHook (wires TelemetryLogger into lifecycle)
"""Tests for ``code_agent.hooks.phase4_hooks.TelemetryHook``."""

from __future__ import annotations

import asyncio
import json

from code_agent.core.session import Session
from code_agent.hooks.phase4_hooks import TelemetryHook
from code_agent.telemetry.logger import TelemetryLogger


def _read_log_lines(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestTelemetryHook:
    def _make(self, tmp_path):
        logger = TelemetryLogger(log_dir=tmp_path)
        hook = TelemetryHook(logger)
        session = Session(system_prompt="T")
        session._session_id = "abc"
        return hook, logger, session

    def test_session_start_binds_and_logs_create(self, tmp_path):
        hook, logger, session = self._make(tmp_path)

        asyncio.run(hook.on_session_start(session))

        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        entries = _read_log_lines(files[0])
        assert entries[0]["event"] == "session_create"
        assert entries[0]["session_id"] == "abc"

    def test_turn_events_logged(self, tmp_path):
        hook, logger, session = self._make(tmp_path)

        async def _drive():
            await hook.on_session_start(session)
            await hook.on_turn_start(session, 0, [])
            session.add_user_message("hi")
            await hook.on_turn_end(session, 0)

        asyncio.run(_drive())

        files = list(tmp_path.glob("*.jsonl"))
        entries = _read_log_lines(files[0])
        events = [e["event"] for e in entries]
        assert events == ["session_create", "turn_start", "turn_complete"]
        turn_complete = entries[-1]
        assert turn_complete["estimated_tokens"] == session.estimated_tokens

    def test_failures_do_not_crash(self, tmp_path):
        # Logger with an unwritable directory → must not raise from the hook.
        hook, logger, session = self._make(tmp_path)
        logger._log_path = tmp_path / "nope" / "x.jsonl"  # parent missing

        async def _drive():
            await hook.on_session_start(session)
            await hook.on_turn_start(session, 0, [])
            await hook.on_turn_end(session, 0)

        asyncio.run(_drive())  # must not raise
