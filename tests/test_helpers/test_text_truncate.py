"""Tests for helpers/text_truncate.py and helpers/token_counter.py."""

from __future__ import annotations

import pytest

from code_agent.helpers.text_truncate import truncate_middle, truncate_with_preview
from code_agent.helpers.token_counter import estimate_tokens, exceeds_threshold


class TestTruncateWithPreview:
    def test_short_text_not_truncated(self):
        text, truncated = truncate_with_preview("hello", 100)
        assert text == "hello"
        assert truncated is False

    def test_exact_max_chars_not_truncated(self):
        text = "a" * 100
        out, truncated = truncate_with_preview(text, 100)
        assert truncated is False
        assert out == text

    def test_truncated_keeps_head_and_footer(self):
        text = "line one\n" * 100
        out, truncated = truncate_with_preview(text, 500, hint="custom hint")
        assert truncated is True
        assert "line one" in out
        assert "custom hint" in out
        assert "total lines" in out
        assert len(out) <= 500

    def test_result_never_exceeds_max_chars(self):
        # Huge text, small budget — footer must be preserved and result bounded.
        text = "x" * 10_000
        out, truncated = truncate_with_preview(text, 200)
        assert truncated is True
        assert len(out) <= 200
        assert "10,000 total" in out or "10000 total" in out

    def test_preview_chars_clamped(self):
        text = "y" * 1000
        out, truncated = truncate_with_preview(text, 300, preview_chars=9999)
        assert truncated is True
        assert len(out) <= 300


class TestTruncateMiddle:
    def test_short_text_not_truncated(self):
        text, truncated = truncate_middle("hello", 100)
        assert truncated is False
        assert text == "hello"

    def test_truncated_keeps_head_tail_and_ellipsis(self):
        text = "BEGIN_" + ("m" * 1000) + "_END"
        out, truncated = truncate_middle(text, 300)
        assert truncated is True
        assert out.startswith("BEGIN_")
        assert out.endswith("_END")
        assert "omitted" in out
        assert "characters omitted" in out

    def test_empty_text(self):
        out, truncated = truncate_middle("", 100)
        assert out == ""
        assert truncated is False


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_ascii_roughly_chars_over_4(self):
        assert estimate_tokens("abcdefgh") == 2  # 8 bytes / 4

    def test_unicode_counts_bytes(self):
        # 你好 = 6 UTF-8 bytes -> 1 token by bytes/4 heuristic.
        assert estimate_tokens("你好") == 1

    def test_large_input(self):
        big = "a" * 1_000_000
        assert estimate_tokens(big) == 250_000


class TestExceedsThreshold:
    def test_below_threshold(self):
        assert exceeds_threshold(1000, 200_000) is False

    def test_at_threshold(self):
        # 85% of 200k = 170k; used == 170k is at the boundary -> True.
        assert exceeds_threshold(170_000, 200_000) is True

    def test_above_threshold(self):
        assert exceeds_threshold(180_000, 200_000) is True

    def test_zero_window_never_exceeds(self):
        assert exceeds_threshold(999, 0) is False
        assert exceeds_threshold(999, -5) is False

    def test_custom_threshold_pct(self):
        assert exceeds_threshold(50_000, 100_000, threshold_pct=50) is True
        assert exceeds_threshold(49_999, 100_000, threshold_pct=50) is False
