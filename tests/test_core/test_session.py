"""Tests for core/session.py: Session state management.

Covers:
- add_user_message / add_assistant_message / add_tool_result
- to_api_messages format (OpenAI compatible)
- _repair_dangling_calls (4-step: collect, detect dupes, inject missing, dedup)
- prompt_count / estimated_tokens properties
"""

from __future__ import annotations

from code_agent.core.messages import ToolCallItem
from code_agent.core.session import Session


class TestSessionBasics:
    def test_init_with_system_prompt(self):
        """Session with system prompt stores it as first message."""
        s = Session(system_prompt="You are helpful.")
        assert s.get_system_prompt() == "You are helpful."
        assert len(s.messages) == 1

    def test_init_without_system_prompt(self):
        """Session without system prompt starts empty."""
        s = Session()
        assert s.get_system_prompt() is None
        assert len(s.messages) == 0

    def test_prompt_count(self):
        """prompt_count increments on each user message."""
        s = Session()
        assert s.prompt_count == 0
        s.add_user_message("Hello")
        assert s.prompt_count == 1
        s.add_user_message("World")
        assert s.prompt_count == 2

    def test_estimated_tokens_increases(self):
        """estimated_tokens grows as messages are added."""
        s = Session()
        initial = s.estimated_tokens
        s.add_user_message("Hello world, this is a test message.")
        assert s.estimated_tokens > initial


class TestReset:
    def test_reset_clears_history_preserves_system(self):
        """reset() drops history/reminders but keeps the system prompt."""
        s = Session(system_prompt="Be helpful.")
        s.add_user_message("User msg")
        s.add_assistant_message(text="Assistant reply")
        before = len(s.messages)
        assert before > 1
        s.reset()
        msgs = s.messages
        assert len(msgs) == 1
        assert msgs[0].role == "system"
        assert msgs[0].content == "Be helpful."
        assert s.prompt_count == 0

    def test_reset_recomputes_tokens(self):
        """reset() resets the token estimate to the system prompt only."""
        s = Session(system_prompt="Be helpful.")
        s.add_user_message("A long user message that should inflate the estimate.")
        assert s.estimated_tokens > 0
        s.reset()
        from code_agent.helpers.token_counter import estimate_tokens
        assert s.estimated_tokens == estimate_tokens("Be helpful.")

    def test_reset_without_system_prompt(self):
        """reset() on a systemless session yields an empty session."""
        s = Session()
        s.add_user_message("hi")
        s.reset()
        assert s.messages == []
        assert s.prompt_count == 0
        assert s.estimated_tokens == 0


class TestUsageLedger:
    def test_record_usage_accumulates(self):
        s = Session(system_prompt="T")
        s.record_usage({
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "cached_prompt_tokens": 40,
            "reasoning_tokens": 10,
        }, model="m1")
        s.record_usage({
            "prompt_tokens": 200,
            "completion_tokens": 30,
        }, model="m1")

        t = s.usage_totals
        assert t.input_tokens == 300
        assert t.output_tokens == 80
        assert t.cached_input_tokens == 40
        assert t.reasoning_tokens == 10
        assert t.total_tokens == 380
        assert t.model_calls == 2
        assert t.by_model["m1"]["total_tokens"] == 380

    def test_record_usage_none_and_empty_ignored(self):
        s = Session()
        s.record_usage(None, model="m")
        s.record_usage({}, model="m")
        assert s.usage_totals.model_calls == 0
        assert s.usage_totals.total_tokens == 0

    def test_record_usage_per_model_split(self):
        s = Session()
        s.record_usage({"prompt_tokens": 10, "completion_tokens": 5}, model="a")
        s.record_usage({"prompt_tokens": 20, "completion_tokens": 10}, model="b")
        assert s.usage_totals.by_model["a"]["total_tokens"] == 15
        assert s.usage_totals.by_model["b"]["total_tokens"] == 30

    def test_reset_clears_usage(self):
        s = Session(system_prompt="T")
        s.record_usage({"prompt_tokens": 10, "completion_tokens": 5}, model="m")
        s.reset()
        t = s.usage_totals
        assert t.model_calls == 0
        assert t.total_tokens == 0
        assert t.by_model == {}

    def test_to_dict_shape(self):
        s = Session()
        s.record_usage({"prompt_tokens": 1, "completion_tokens": 2}, model="m")
        d = s.usage_totals.to_dict()
        assert d["input_tokens"] == 1
        assert d["output_tokens"] == 2
        assert d["total_tokens"] == 3
        assert d["model_calls"] == 1


class TestToApiMessages:
    def test_system_message_format(self):
        """System message converts to OpenAI format."""
        s = Session(system_prompt="Be helpful.")
        msgs = s.to_api_messages()
        assert msgs[0] == {"role": "system", "content": "Be helpful."}

    def test_user_message_format(self):
        """User message converts to OpenAI format."""
        s = Session()
        s.add_user_message("Read this file")
        msgs = s.to_api_messages()
        assert msgs[0] == {"role": "user", "content": "Read this file"}

    def test_assistant_text_format(self):
        """Assistant text-only message converts correctly."""
        s = Session()
        s.add_assistant_message(text="Here is the answer.")
        msgs = s.to_api_messages()
        assert msgs[0] == {"role": "assistant", "content": "Here is the answer."}

    def test_assistant_tool_calls_format(self):
        """Assistant message with tool_calls converts to OpenAI format."""
        s = Session()
        tc = ToolCallItem(id="call_1", name="file_read", arguments='{"path": "a.py"}')
        s.add_assistant_message(tool_calls=[tc])
        msgs = s.to_api_messages()

        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["tool_calls"] == [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": '{"path": "a.py"}',
                },
            }
        ]

    def test_tool_result_format(self):
        """Tool result message converts to OpenAI format."""
        s = Session()
        s.add_tool_result("call_1", "file content here", is_error=False)
        msgs = s.to_api_messages()
        assert msgs[0] == {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "file content here",
        }

    def test_to_api_messages_repairs_dangling_calls_on_read(self):
        """[Phase 6] Reading messages also repairs orphan tool calls."""
        from code_agent.core.messages import ToolCallItem

        s = Session(system_prompt="T")
        s.add_assistant_message(
            tool_calls=[ToolCallItem(id="c1", name="echo", arguments="{}")],
        )
        msgs = s.to_api_messages()
        roles = [m["role"] for m in msgs]
        assert "tool" in roles  # placeholder injected on the read boundary

    def test_full_conversation_format(self):
        """Multi-turn conversation produces correct message sequence."""
        s = Session(system_prompt="sys")
        s.add_user_message("user msg")
        tc = ToolCallItem(id="c1", name="shell_exec", arguments='{"command": "ls"}')
        s.add_assistant_message(tool_calls=[tc])
        s.add_tool_result("c1", "file1.py\nfile2.py")
        s.add_assistant_message(text="Done!")

        msgs = s.to_api_messages()
        assert len(msgs) == 5
        assert [m["role"] for m in msgs] == [
            "system", "user", "assistant", "tool", "assistant"
        ]


class TestRepairDanglingCalls:
    def test_no_tool_calls_no_repair(self):
        """Session without tool calls needs no repair."""
        s = Session()
        s.add_user_message("Hello")
        s.add_assistant_message(text="Hi!")
        # Trigger repair via next user message
        s.add_user_message("More")
        # No placeholder injected: user, assistant, user
        assert len(s.messages) == 3

    def test_paired_calls_no_repair(self):
        """Fully paired tool calls need no repair."""
        s = Session()
        tc = ToolCallItem(id="c1", name="file_read", arguments="{}")
        s.add_assistant_message(tool_calls=[tc])
        s.add_tool_result("c1", "content")
        # Trigger repair
        s.add_user_message("next")
        # No extra messages injected: assistant + tool_result + user
        assert len(s.messages) == 3

    def test_dangling_call_gets_placeholder(self):
        """Unpaired tool call gets placeholder ToolResult on next user message."""
        s = Session()
        tc = ToolCallItem(id="c1", name="file_read", arguments="{}")
        s.add_assistant_message(tool_calls=[tc])
        # No tool result added — dangling!
        s.add_user_message("next prompt")

        # Repair should have injected a placeholder before the user message
        api_msgs = s.to_api_messages()
        tool_msgs = [m for m in api_msgs if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "c1"
        assert "interrupted" in tool_msgs[0]["content"].lower()

    def test_multiple_dangling_calls(self):
        """Multiple unpaired calls each get a placeholder."""
        s = Session()
        tc1 = ToolCallItem(id="c1", name="file_read", arguments="{}")
        tc2 = ToolCallItem(id="c2", name="shell_exec", arguments="{}")
        s.add_assistant_message(tool_calls=[tc1, tc2])
        # Only provide result for c1
        s.add_tool_result("c1", "ok")
        # Trigger repair
        s.add_user_message("next")

        api_msgs = s.to_api_messages()
        tool_msgs = [m for m in api_msgs if m["role"] == "tool"]
        assert len(tool_msgs) == 2
        ids = {m["tool_call_id"] for m in tool_msgs}
        assert ids == {"c1", "c2"}

    def test_duplicate_results_deduped(self):
        """Duplicate ToolResults for same call_id are removed (keep first)."""
        s = Session()
        tc = ToolCallItem(id="c1", name="file_read", arguments="{}")
        s.add_assistant_message(tool_calls=[tc])
        s.add_tool_result("c1", "first result")
        s.add_tool_result("c1", "duplicate result")
        # Trigger repair (dedup step)
        s.add_user_message("next")

        api_msgs = s.to_api_messages()
        tool_msgs = [m for m in api_msgs if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["content"] == "first result"


class TestHistoryReplacedFlag:
    """[2026-08-05] Compaction rewrite signal for persistence hooks."""

    def test_false_by_default(self):
        assert Session()._history_replaced is False

    def test_replace_history_sets_flag(self):
        from code_agent.core.messages import SystemMessage, UserMessage

        s = Session(system_prompt="S")
        s.add_user_message("hello")
        s.replace_history([
            SystemMessage(content="S"),
            UserMessage(content="compacted"),
        ])
        assert s._history_replaced is True

    def test_reset_clears_flag(self):
        from code_agent.core.messages import SystemMessage, UserMessage

        s = Session(system_prompt="S")
        s.add_user_message("hello")
        s.replace_history([
            SystemMessage(content="S"),
            UserMessage(content="compacted"),
        ])
        assert s._history_replaced is True
        s.reset()
        assert s._history_replaced is False
