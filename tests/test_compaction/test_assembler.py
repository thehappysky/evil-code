"""Tests for compaction/assembler.py — assemble_compacted_history."""

import pytest

from code_agent.compaction.assembler import (
    assemble_compacted_history,
    count_preserved_turns,
)


class TestAssembleCompactedHistory:
    def test_basic_structure(self):
        result = assemble_compacted_history(
            system_prompt="You are an AI coding agent.",
            summary="User asked about sessions. Implemented JSONL storage.",
            recent_messages=[
                {"role": "user", "content": "What's next?"},
                {"role": "assistant", "content": "We should add tests."},
            ],
        )
        # [system, user(summary), recent_msgs...]
        assert len(result) == 4
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are an AI coding agent."
        assert result[1]["role"] == "user"
        assert "conversation-history-summary" in result[1]["content"]
        assert result[2]["role"] == "user"
        assert result[3]["role"] == "assistant"

    def test_with_project_rules(self):
        result = assemble_compacted_history(
            system_prompt="Agent.",
            summary="Summary.",
            recent_messages=[],
            project_rules="# Rules\nUse PEP 8.",
        )
        assert len(result) == 3  # system, rules, summary
        assert "project-context" in result[1]["content"]

    def test_with_reminders(self):
        result = assemble_compacted_history(
            system_prompt="Agent.",
            summary="Summary.",
            recent_messages=[],
            reminders="<system-reminder>\nTodo: write tests\n</system-reminder>",
        )
        assert len(result) == 3  # system, summary, reminders
        assert "system-reminder" in result[-1]["content"]

    def test_with_all_sections(self):
        result = assemble_compacted_history(
            system_prompt="Agent.",
            summary="Summary.",
            recent_messages=[
                {"role": "user", "content": "Latest question"},
            ],
            reminders="<reminder>",
            project_rules="# Rules",
        )
        roles = [m["role"] for m in result]
        assert roles == ["system", "user", "user", "user", "user"]
        # system, rules, summary, recent, reminders

    def test_summary_contains_xml_tags(self):
        result = assemble_compacted_history(
            system_prompt="S", summary="Important summary.", recent_messages=[],
        )
        summary_content = result[1]["content"]
        assert "<conversation-history-summary>" in summary_content
        assert "Important summary." in summary_content
        assert "</conversation-history-summary>" in summary_content


class TestCountPreservedTurns:
    def test_empty(self):
        to_sum, pres = count_preserved_turns([], max_recent_tokens=1000)
        assert to_sum == []
        assert pres == []

    def test_all_preserved_when_fit(self):
        msgs = [
            {"role": "system", "content": "x" * 10},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        to_sum, pres = count_preserved_turns(msgs, max_recent_tokens=1000)
        assert len(pres) == 2  # user + assistant (system excluded)
        assert len(to_sum) == 1  # Only system

    def test_partial_preserve(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1" * 50},
            {"role": "assistant", "content": "a1" * 50},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
        ]
        # Only last turn should fit within small budget
        to_sum, pres = count_preserved_turns(msgs, max_recent_tokens=10)
        assert len(pres) == 2  # q2 + a2
        # to_sum should contain system + q1 + a1
        assert len(to_sum) >= 3

    def test_custom_estimator(self):
        msgs = [
            {"role": "user", "content": "hello world"},
        ]
        # Each word = 1 token
        to_sum, pres = count_preserved_turns(
            msgs, max_recent_tokens=5,
            token_estimator=lambda s: len(s.split()),
        )
        assert len(pres) == 1  # "hello world" = 2 tokens <= 5

    def test_nothing_fits_returns_empty_preserved(self):
        """When the budget fits nothing, preserve nothing (no snap crash)."""
        msgs = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u" * 50},
            {"role": "assistant", "content": "a" * 50},
        ]
        to_sum, pres = count_preserved_turns(msgs, max_recent_tokens=3)
        assert pres == []
        assert len(to_sum) == len(msgs)

    def test_snaps_boundary_off_orphan_tool_result(self):
        """A preserved window that would start on a bare tool result is
        extended backward to include its owning assistant (Rust
        snap_to_safe_boundary — avoids orphan tool messages / API 400)."""
        msgs = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u" * 40},
            {"role": "assistant", "content": "a" * 40},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "echo", "arguments": "{}"}},
            ], "content": "m" * 60},   # large → excluded by the budget
            {"role": "tool", "tool_call_id": "c1", "content": "tr"},
            {"role": "assistant", "content": "final"},
        ]

        def est(content: str) -> int:
            return len(content)

        to_sum, pres = count_preserved_turns(
            msgs, max_recent_tokens=8, token_estimator=est,
        )

        # Without snapping, "tr" + "final" would fit and the preserved window
        # would open on the bare tool message. With snapping, the owning
        # assistant is pulled in so the window starts on an assistant.
        assert pres and pres[0].get("role") == "assistant"
        assert any("tool_calls" in m for m in pres)
        # The summarize pile never ends on a tool result (no orphan).
        assert not (to_sum and to_sum[-1].get("role") == "tool")
