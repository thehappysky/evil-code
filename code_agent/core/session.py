"""Session - conversation state management.

Logic reference: Rust version ChatStateActor (chat-state/src/actor/) + ChatState struct.

No Actor pattern needed: Python asyncio single-thread means Session's caller (Agent)
is in the same coroutine — no concurrent mutation possible.

Key features:
- _repair_dangling_calls(): equivalent to Rust's ensure_conversation_integrity()
- Token estimation via helpers/token_counter bytes/4 heuristic
- to_api_messages(): convert internal messages to OpenAI API format
"""

from __future__ import annotations

from code_agent.core.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolCallItem,
    ToolResultMessage,
    UserMessage,
)
from code_agent.helpers.token_counter import estimate_tokens
from code_agent.telemetry.usage import UsageTotals


class Session:
    """Manages conversation state for a single Agent session.

    Attributes:
        prompt_count: Number of user prompts processed.
        estimated_tokens: Estimated token count (bytes/4 heuristic).
        usage_totals: Real token usage reported by the API.
    """

    def __init__(self, system_prompt: str | None = None):
        self._messages: list[Message] = []
        self._prompt_count: int = 0
        self._estimated_tokens: int = 0
        self._usage_totals: UsageTotals = UsageTotals()
        # [2026-08-05] Set by replace_history(): persistence hooks rewrite the
        # on-disk JSONL in full instead of appending after a compaction.
        self._history_replaced: bool = False

        if system_prompt:
            self._messages.append(SystemMessage(content=system_prompt))
            self._estimated_tokens += estimate_tokens(system_prompt)

    @property
    def prompt_count(self) -> int:
        """Number of user prompts processed."""
        return self._prompt_count

    @property
    def estimated_tokens(self) -> int:
        """Estimated token count (bytes/4 heuristic)."""
        return self._estimated_tokens

    @property
    def usage_totals(self) -> UsageTotals:
        """Real API-reported token usage accumulated across turns."""
        return self._usage_totals

    def record_usage(self, usage: dict | None, model: str = "") -> None:
        """Accumulate a turn's real API usage into the session ledger.

        Delegates to ``UsageTotals.accumulate`` (telemetry/usage.py).
        Independent of ``estimated_tokens`` (bytes/4 heuristic), which remains
        the compaction-gate signal.
        """
        self._usage_totals.accumulate(usage, model)

    @property
    def messages(self) -> list[Message]:
        """Read-only access to message list (for hooks/inspection)."""
        return self._messages

    def add_user_message(self, content: str) -> None:
        """Add a user message. Triggers dangling call repair before write."""
        self._repair_dangling_calls()
        self._messages.append(UserMessage(content=content))
        self._prompt_count += 1
        self._estimated_tokens += estimate_tokens(content)

    def add_assistant_message(
        self,
        text: str | None = None,
        tool_calls: list[ToolCallItem] | None = None,
    ) -> None:
        """Add an assistant message. Triggers dangling call repair before write."""
        self._repair_dangling_calls()
        msg = AssistantMessage(content=text, tool_calls=tool_calls)
        self._messages.append(msg)
        if text:
            self._estimated_tokens += estimate_tokens(text)
        if tool_calls:
            for tc in tool_calls:
                self._estimated_tokens += estimate_tokens(tc.name + tc.arguments)

    def add_tool_result(
        self, call_id: str, content: str, is_error: bool = False
    ) -> None:
        """Add a tool result message."""
        self._messages.append(
            ToolResultMessage(
                tool_call_id=call_id, content=content, is_error=is_error
            )
        )
        self._estimated_tokens += estimate_tokens(content)

    def get_system_prompt(self) -> str | None:
        """Get the system prompt (first message if it's a SystemMessage)."""
        if self._messages and isinstance(self._messages[0], SystemMessage):
            return self._messages[0].content
        return None

    def reset(self) -> None:
        """Clear all messages except the system prompt; reset counters.

        Used by ``/clear`` to start a fresh conversation on the same Session
        (REPL semantics), instead of only clearing the rendered transcript.

        The first message, if a SystemMessage, is preserved; everything else
        (history, memory context, reminders) is dropped and the token /
        prompt counters are reset.
        """
        self._messages = [
            m for m in self._messages if isinstance(m, SystemMessage)
        ]
        self._prompt_count = 0
        self._estimated_tokens = 0
        self._usage_totals = UsageTotals()
        self._history_replaced = False
        if self._messages and isinstance(self._messages[0], SystemMessage):
            self._estimated_tokens = estimate_tokens(self._messages[0].content)

    def replace_history(self, items: list[Message]) -> None:
        """Bulk replace the entire conversation history.

        Used after compaction to swap full history with a compacted version.
        Equivalent to clearing and re-adding, but done in a single atomic step
        to avoid O(n) persistence writes.

        Steps:
        1. Validate items is non-empty and starts with SystemMessage
        2. Replace _messages with the new list
        3. Recalculate token estimate
        4. Repair dangling calls in the new history

        Args:
            items: New list of Message objects. Must start with SystemMessage.

        Raises:
            ValueError: If items is empty or doesn't start with SystemMessage.
        """
        if not items:
            raise ValueError("Cannot replace history with empty list")
        if not isinstance(items[0], SystemMessage):
            raise ValueError(
                "Compacted history must start with SystemMessage, "
                f"got {type(items[0]).__name__}"
            )

        self._messages = list(items)
        # Signal persistence hooks that the file must be rewritten, not appended.
        self._history_replaced = True
        # Recalculate token estimate
        self._estimated_tokens = 0
        for msg in self._messages:
            if isinstance(msg, SystemMessage):
                self._estimated_tokens += estimate_tokens(msg.content)
            elif isinstance(msg, UserMessage):
                self._estimated_tokens += estimate_tokens(msg.content)
            elif isinstance(msg, AssistantMessage):
                if msg.content:
                    self._estimated_tokens += estimate_tokens(msg.content)
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        self._estimated_tokens += estimate_tokens(
                            tc.name + tc.arguments
                        )
            elif isinstance(msg, ToolResultMessage):
                self._estimated_tokens += estimate_tokens(msg.content)

        # Repair any dangling calls in the new history
        self._repair_dangling_calls()

    @classmethod
    def from_dicts(
        cls, messages: list[dict], system_prompt: str | None = None
    ) -> "Session":
        """Reconstruct a Session from stored message dicts.

        Used when loading a session from JSONL storage.

        Args:
            messages: List of message dicts (from JSONL).
            system_prompt: Optional system prompt override.
                If None and first dict is a system message, uses that.

        Returns:
            Reconstructed Session instance.
        """
        session = cls(system_prompt=None)

        for msg_dict in messages:
            role = msg_dict.get("role", "")

            if role == "system":
                session._messages.append(
                    SystemMessage(content=msg_dict.get("content", ""))
                )
            elif role == "user":
                session._messages.append(
                    UserMessage(content=msg_dict.get("content", ""))
                )
            elif role == "assistant":
                tool_calls = None
                if "tool_calls" in msg_dict and msg_dict["tool_calls"]:
                    tool_calls = [
                        ToolCallItem(
                            id=tc.get("id", ""),
                            name=tc.get("function", {}).get("name", tc.get("name", "")),
                            arguments=tc.get("function", {}).get("arguments", tc.get("arguments", "")),
                        )
                        for tc in msg_dict["tool_calls"]
                    ]
                session._messages.append(
                    AssistantMessage(
                        content=msg_dict.get("content"),
                        tool_calls=tool_calls,
                    )
                )
            elif role == "tool":
                session._messages.append(
                    ToolResultMessage(
                        tool_call_id=msg_dict.get("tool_call_id", ""),
                        content=msg_dict.get("content", ""),
                        is_error=msg_dict.get("is_error", False),
                    )
                )

        # Override system prompt if provided
        if system_prompt:
            if session._messages and isinstance(
                session._messages[0], SystemMessage
            ):
                session._messages[0] = SystemMessage(content=system_prompt)
            else:
                session._messages.insert(0, SystemMessage(content=system_prompt))

        # Recalculate tokens
        session._estimated_tokens = 0
        for msg in session._messages:
            if hasattr(msg, "content") and msg.content:
                session._estimated_tokens += estimate_tokens(str(msg.content))

        return session

    def to_api_messages(self) -> list[dict]:
        """Convert internal messages to OpenAI API format.

        [Phase 6] The read boundary also repairs dangling tool calls, so the
        model API never receives an orphan tool message even if a write path
        was interrupted between mutations.

        Returns:
            List of dicts matching OpenAI chat completion message format.
        """
        self._repair_dangling_calls()
        api_messages: list[dict] = []

        for msg in self._messages:
            if isinstance(msg, SystemMessage):
                api_messages.append({"role": "system", "content": msg.content})

            elif isinstance(msg, UserMessage):
                api_messages.append({"role": "user", "content": msg.content})

            elif isinstance(msg, AssistantMessage):
                entry: dict = {"role": "assistant"}
                if msg.content:
                    entry["content"] = msg.content
                if msg.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": tc.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                api_messages.append(entry)

            elif isinstance(msg, ToolResultMessage):
                api_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.tool_call_id,
                        "content": msg.content,
                    }
                )

        return api_messages

    def _repair_dangling_calls(self) -> None:
        """Repair unpaired tool calls (dangling calls).

        Equivalent to Rust's ChatStateActor::ensure_conversation_integrity().

        Steps:
        1. Collect all tool_call IDs from AssistantMessages
        2. Collect all tool_call_ids from ToolResultMessages
        3. For missing results: inject placeholder ToolResult with is_error=True
        4. Deduplicate: remove extra ToolResults for same call_id
        """
        # Collect all tool call IDs from assistant messages
        expected_ids: list[str] = []
        for msg in self._messages:
            if isinstance(msg, AssistantMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    expected_ids.append(tc.id)

        if not expected_ids:
            return

        # Collect all result IDs and detect duplicates
        result_ids: set[str] = set()
        duplicate_indices: list[int] = []
        for i, msg in enumerate(self._messages):
            if isinstance(msg, ToolResultMessage):
                if msg.tool_call_id in result_ids:
                    duplicate_indices.append(i)
                else:
                    result_ids.add(msg.tool_call_id)

        # Step 4: Remove duplicate ToolResults (keep first occurrence)
        if duplicate_indices:
            for idx in reversed(duplicate_indices):
                del self._messages[idx]

        # Find missing results
        missing = [cid for cid in expected_ids if cid not in result_ids]

        if missing:
            # Step 3: Inject placeholder results at the end
            for call_id in missing:
                self._messages.append(
                    ToolResultMessage(
                        tool_call_id=call_id,
                        content="[Tool call was interrupted — no result available]",
                        is_error=True,
                    )
                )
