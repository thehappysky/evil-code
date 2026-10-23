"""Tests for telemetry/usage_stats.py (re-export of UsageTracker)."""

from __future__ import annotations

import code_agent.telemetry.logger as logger
import code_agent.telemetry.usage_stats as usage_stats


def test_reexports_logger_usage_tracker():
    """usage_stats.UsageTracker is the same class defined in logger.py."""
    assert usage_stats.UsageTracker is logger.UsageTracker


def test_reexport_is_in_all():
    """The convenience module exposes UsageTracker via __all__."""
    assert "UsageTracker" in usage_stats.__all__
    assert len(usage_stats.__all__) == 1
