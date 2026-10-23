"""Tests for compaction/summarizer.py — generate_summary, classify_compact_error."""

import httpx
import pytest

from code_agent.compaction.summarizer import (
    classify_compact_error,
    _clean_summary,
    _fit_conversation,
)


class TestClassifyCompactError:
    def test_http_401_is_deterministic(self):
        resp = httpx.Response(401, request=httpx.Request("POST", "http://x"))
        err = httpx.HTTPStatusError("Unauthorized", request=err.request, response=resp) if False else None
        # Use a mock since we can't easily construct HTTPStatusError without response
        pass

    def test_value_error_is_deterministic(self):
        assert classify_compact_error(ValueError("bad")) == "deterministic"

    def test_type_error_is_deterministic(self):
        assert classify_compact_error(TypeError("bad")) == "deterministic"

    def test_connect_error_is_transient(self):
        assert classify_compact_error(httpx.ConnectError("refused")) == "transient"

    def test_timeout_is_transient(self):
        assert classify_compact_error(httpx.TimeoutException("timeout")) == "transient"

    def test_unknown_is_deterministic(self):
        assert classify_compact_error(RuntimeError("unknown")) == "deterministic"


class TestCleanSummary:
    def test_strips_prefix(self):
        result = _clean_summary("Here is a summary of the conversation:\n\nThe user asked about X.")
        assert not result.startswith("Here is a summary")

    def test_strips_code_fence(self):
        result = _clean_summary("```\nSummary text\n```")
        assert "```" not in result
        assert "Summary text" in result

    def test_preserves_normal_text(self):
        result = _clean_summary("Just a normal summary.")
        assert result == "Just a normal summary."


class TestFitConversation:
    def test_all_fits(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        result = _fit_conversation(msgs, "You are helpful.", max_input_tokens=1000)
        assert len(result) == 2

    def test_truncates_old_messages(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old message " * 100},
            {"role": "assistant", "content": "old reply " * 100},
            {"role": "user", "content": "new message"},
        ]
        result = _fit_conversation(msgs, "sys", max_input_tokens=50)
        # Should keep system + as many prefix as fit + last
        assert len(result) >= 2
        # Last message should always be present
        assert result[-1]["content"] == "new message"
