"""Session management verification script.

Usage:
    cd D:/Pycharm_py/evil-code
    .venv/Scripts/python.exe verify_session.py

Each test runs independently, output PASS/FAIL.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from code_agent.core.session import Session
from code_agent.core.messages import (
    ToolCallItem,
    SystemMessage,
    UserMessage,
    AssistantMessage,
    ToolResultMessage,
)

_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}  --  {detail}")


# ============================================================
# Test 1: Message Storage - 4 message types
# ============================================================
print("\n" + "=" * 60)
print("1. Message Storage: System / User / Assistant / ToolResult")
print("=" * 60)

s = Session(system_prompt="You are a Python assistant.")

check("messages[0] is SystemMessage",
      isinstance(s.messages[0], SystemMessage))
check("SystemMessage content matches",
      s.messages[0].content == "You are a Python assistant.")

s.add_user_message("Read README.md for me")
check("messages[1] is UserMessage",
      isinstance(s.messages[1], UserMessage))
check("UserMessage content matches",
      s.messages[1].content == "Read README.md for me")

tc = ToolCallItem(id="call_001", name="file_read",
                  arguments='{"file_path": "README.md"}')
s.add_assistant_message(tool_calls=[tc])
check("messages[2] is AssistantMessage",
      isinstance(s.messages[2], AssistantMessage))
check("AssistantMessage has 1 tool_call",
      s.messages[2].tool_calls is not None
      and len(s.messages[2].tool_calls) == 1)
check("ToolCallItem.name == file_read",
      s.messages[2].tool_calls[0].name == "file_read")

s.add_tool_result("call_001", "# My Project\n\nHello World", is_error=False)
check("messages[3] is ToolResultMessage",
      isinstance(s.messages[3], ToolResultMessage))
check("ToolResultMessage.tool_call_id matches",
      s.messages[3].tool_call_id == "call_001")
check("ToolResultMessage.content matches",
      s.messages[3].content == "# My Project\n\nHello World")

s.add_assistant_message(text="README.md is a markdown project.")
check("messages[4] is text-only AssistantMessage",
      isinstance(s.messages[4], AssistantMessage)
      and s.messages[4].content is not None
      and s.messages[4].tool_calls is None)

print(f"\n  Message chain: {[type(m).__name__ for m in s.messages]}")

# ============================================================
# Test 2: to_api_messages() - OpenAI format conversion
# ============================================================
print("\n" + "=" * 60)
print("2. to_api_messages() -> OpenAI Format")
print("=" * 60)

api_msgs = s.to_api_messages()
check("5 messages in total", len(api_msgs) == 5)

roles = [m["role"] for m in api_msgs]
expected = ["system", "user", "assistant", "tool", "assistant"]
check(f"Role sequence: {roles}", roles == expected)

tool_call_msg = api_msgs[2]
check("assistant msg has tool_calls array",
      "tool_calls" in tool_call_msg)
check("tool_calls[0].type == function",
      tool_call_msg["tool_calls"][0]["type"] == "function")
check("tool_calls[0].function.name == file_read",
      tool_call_msg["tool_calls"][0]["function"]["name"] == "file_read")
check("tool_calls[0].function.arguments is JSON string",
      tool_call_msg["tool_calls"][0]["function"]["arguments"]
      == '{"file_path": "README.md"}')

tool_msg = api_msgs[3]
check("tool msg has tool_call_id",
      tool_msg["tool_call_id"] == "call_001")
check("tool msg role == tool",
      tool_msg["role"] == "tool")

print(f"\n  API message format preview:")
for m in api_msgs:
    role = m["role"]
    preview = str(m.get("content", "") or m.get("tool_calls", ""))[:60]
    print(f"    [{role}] {preview}...")

# ============================================================
# Test 3: Dangling Call Repair - unpaired tool_call
# ============================================================
print("\n" + "=" * 60)
print("3. Dangling Call Repair: unpaired tool_call")
print("=" * 60)

s2 = Session()
tc = ToolCallItem(id="orphan_1", name="shell_exec",
                  arguments='{"command": "ls"}')
s2.add_assistant_message(tool_calls=[tc])
# NOTE: deliberately NOT calling add_tool_result! This is a dangling call.

check("Before repair: 1 message (assistant only)",
      len(s2.messages) == 1)

# Trigger repair by adding next user message
s2.add_user_message("Next question")

check("After repair: 3 messages (assistant + placeholder + user)",
      len(s2.messages) == 3)
check("messages[1] is ToolResultMessage",
      isinstance(s2.messages[1], ToolResultMessage))
check("Placeholder contains 'interrupted'",
      "interrupted" in s2.messages[1].content.lower())
check("Placeholder is_error=True",
      s2.messages[1].is_error is True)

print(f"\n  Placeholder content: {s2.messages[1].content}")

# ============================================================
# Test 4: Duplicate ToolResult dedup
# ============================================================
print("\n" + "=" * 60)
print("4. Dangling Repair: Duplicate ToolResult dedup")
print("=" * 60)

s3 = Session()
tc = ToolCallItem(id="dup_1", name="file_read",
                  arguments='{"file_path": "a.py"}')
s3.add_assistant_message(tool_calls=[tc])
s3.add_tool_result("dup_1", "first result")
s3.add_tool_result("dup_1", "duplicate - should be removed")

check("Before dedup: 3 messages", len(s3.messages) == 3)

s3.add_user_message("next")  # trigger repair

check("After dedup: 3 messages (assistant + first result + user)",
      len(s3.messages) == 3)
tool_msgs = [m for m in s3.messages if isinstance(m, ToolResultMessage)]
check("Only 1 ToolResult remains",
      len(tool_msgs) == 1)
check("First result preserved",
      tool_msgs[0].content == "first result")

# ============================================================
# Test 5: Token estimation (bytes/4)
# ============================================================
print("\n" + "=" * 60)
print("5. Token Estimation (bytes/4 heuristic)")
print("=" * 60)

s4 = Session()
t0 = s4.estimated_tokens
check("Empty session tokens == 0", t0 == 0)

s4.add_user_message("Hello World")  # 11 bytes -> ~2 tokens
t1 = s4.estimated_tokens
check("Tokens grow after adding 'Hello World'", t1 > t0)
print(f"    'Hello World' -> est {t1} tokens (actual ~2)")

s4.add_user_message("A longer message to verify token estimation works correctly.")
t2 = s4.estimated_tokens
check("Tokens grow further after longer message", t2 > t1)
print(f"    After longer msg -> est {t2} tokens")

s4.add_assistant_message(
    text="Response with tool calls.",
    tool_calls=[ToolCallItem(id="c1", name="file_read",
                              arguments='{"file_path": "test.py"}')],
)
t3 = s4.estimated_tokens
check("Assistant + tool_calls grows tokens more", t3 > t2)
print(f"    After assistant+tool_calls -> est {t3} tokens")

# Verify bytes/4 logic
from code_agent.helpers.token_counter import estimate_tokens
check("'hello' 5 bytes // 4 = 1",
      estimate_tokens("hello") == 1)
check("'hello world!!' 13 bytes // 4 = 3",
      estimate_tokens("hello world!!") == 3)

# ============================================================
# Test 6: prompt_count counter
# ============================================================
print("\n" + "=" * 60)
print("6. prompt_count - User input counter")
print("=" * 60)

s5 = Session()
check("Initial prompt_count == 0", s5.prompt_count == 0)

s5.add_user_message("First question")
check("After 1st == 1", s5.prompt_count == 1)

s5.add_user_message("Second question")
s5.add_user_message("Third question")
check("After 3rd == 3", s5.prompt_count == 3)

# ============================================================
print("\n" + "=" * 60)
print(f"Result: {_passed} PASS / {_failed} FAIL")
print("=" * 60)

if _failed > 0:
    print("\nSome tests FAILED. Check the FAIL lines above.")
    sys.exit(1)
else:
    print("\nAll passed! Session management is working correctly.")
