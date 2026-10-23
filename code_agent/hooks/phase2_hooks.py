"""Phase 2 AgentHook implementations.

Each hook wraps a Phase 2 module and adapts it to the AgentHook interface.
All hooks follow the exception handling contract:
    - on_session_start / on_turn_start / on_turn_end / on_tool_result:
      catch + log.warning + return None (degrade gracefully)
    - on_tool_call: catch + log.error + return DENY (fail-safe)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from code_agent.compaction.assembler import (
    assemble_compacted_history,
    count_preserved_turns,
)
from code_agent.compaction.summarizer import generate_summary
from code_agent.core.hooks import AgentHook, HookDecision
from code_agent.core.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolCallItem,
    ToolResultMessage,
    UserMessage,
)
from code_agent.prompts.reminders import ReminderInjector
from code_agent.core.session import Session
from code_agent.helpers.token_counter import estimate_tokens
from code_agent.memory.search import build_session_query, search_memory
from code_agent.storage.models import SessionMeta
from code_agent.storage.session_store import _generate_session_id
from code_agent.tools.result import ToolResult

if TYPE_CHECKING:
    from code_agent.api.client import ChatClient
    from code_agent.compaction.policy import CompactionPolicy
    from code_agent.memory.index_db import MemoryIndex
    from code_agent.storage.session_store import SessionStore

logger = logging.getLogger(__name__)


class MemoryHook:
    """Inject cross-session memory at session start.

    Wraps MemoryIndex. On session_start, searches for memories
    relevant to the first user message and injects them as a
    synthetic user message.

    Exception handling: SQLite errors → log.warning + skip injection.
    Agent continues without memory context.
    """

    def __init__(self, memory_index: "MemoryIndex"):
        self._index = memory_index
        self._injector = ReminderInjector()
        self._injected: bool = False  # Only inject once per session

    async def on_session_start(self, session: Session) -> None:
        if self._injected:
            return

        try:
            # [MEM-05] 2026-08-09: Query construction (last user msg,
            # truncate 200) delegated to memory.build_session_query.
            query = build_session_query(session.messages)
            if query is None:
                return

            results = await search_memory(self._index, query, limit=5)
            reminder = self._injector.memory_reminder(
                [{"path": r.path, "text": r.text, "source": r.source}
                 for r in results]
            )

            if reminder:
                session.add_user_message(reminder)
                logger.info(
                    "Injected %d memory results for query '%s'",
                    len(results), query[:50],
                )

            self._injected = True

        except Exception:
            logger.warning("Memory injection failed", exc_info=True)


class CompactionHook:
    """Trigger auto-compaction when token threshold exceeded.

    Wraps CompactionPolicy + ChatClient. On each turn_start, checks if
    the estimated token count exceeds the policy threshold. If so:
    1. Generates a summary via LLM
    2. Assembles compacted history
    3. Replaces session history
    4. Injects post-compaction reminder

    Exception handling: LLM call failure → log.warning + skip compaction.
    Agent continues with full conversation (may hit context limit).
    """

    def __init__(
        self,
        policy: "CompactionPolicy",
        client: "ChatClient",
        *,
        preserve_turns: int = 4,
        edit_tracker: "EditTrackerHook | None" = None,
    ):
        self._policy = policy
        self._client = client
        self._preserve_turns = preserve_turns
        self._edit_tracker = edit_tracker
        self._injector = ReminderInjector()
        self._compaction_count: int = 0

    async def on_turn_start(
        self, session: Session, turn: int, tools: list[dict]
    ) -> None:
        try:
            if not self._policy.should_compact(session.estimated_tokens):
                return

            logger.info(
                "Compaction triggered at turn %d (%d tokens)",
                turn, session.estimated_tokens,
            )

            # Get full conversation in API format
            conversation = session.to_api_messages()
            system_prompt = session.get_system_prompt() or ""

            # Split into to-summarize and to-preserve
            # Preserve last N user-assistant exchange pairs
            recent_tokens = self._policy.target_budget()
            to_summarize, to_preserve = count_preserved_turns(
                conversation,
                max_recent_tokens=recent_tokens,
                token_estimator=lambda s: len(s) // 4,
            )

            if not to_summarize:
                logger.debug("Nothing to summarize; skipping compaction")
                return

            # Generate summary
            summary = await generate_summary(
                self._client, to_summarize, system_prompt,
                context_window=self._policy.context_window,
            )

            # Assemble compacted history
            # [2026-08-05] The edit tracker is now actually wired in: previously
            # this read a nonexistent attribute, so edited files never appeared
            # in the post-compaction reminder.
            edited_files = (
                self._edit_tracker.get_edited_files()
                if self._edit_tracker is not None else None
            )
            assembled = assemble_compacted_history(
                system_prompt=system_prompt,
                summary=summary,
                recent_messages=to_preserve,
                reminders=self._injector.compaction_reminder(
                    edited_files=edited_files,
                ),
            )

            # Convert dicts to Message objects
            new_messages: list[Message] = []
            for msg in assembled:
                role = msg.get("role", "")
                if role == "system":
                    new_messages.append(
                        SystemMessage(content=msg.get("content", ""))
                    )
                elif role == "user":
                    new_messages.append(
                        UserMessage(content=msg.get("content", ""))
                    )
                elif role == "assistant":
                    tool_calls = None
                    if "tool_calls" in msg:
                        tool_calls = [
                            ToolCallItem(
                                id=tc.get("id", ""),
                                name=tc.get("function", {}).get("name", ""),
                                arguments=tc.get("function", {}).get("arguments", ""),
                            )
                            for tc in msg["tool_calls"]
                        ]
                    new_messages.append(
                        AssistantMessage(
                            content=msg.get("content"),
                            tool_calls=tool_calls,
                        )
                    )
                elif role == "tool":
                    new_messages.append(
                        ToolResultMessage(
                            tool_call_id=msg.get("tool_call_id", ""),
                            content=msg.get("content", ""),
                        )
                    )

            # [Phase 6] 2026-08-04: Reduction Guard (Rust Reduction Guard,
            # 压缩率<20% 拒绝) — skip compaction if it barely shrinks the
            # conversation; otherwise we lose information for no gain.
            old_tokens = session.estimated_tokens
            new_tokens = _estimate_messages(new_messages)
            if old_tokens > 0 and new_tokens >= old_tokens * 0.8:
                logger.info(
                    "Compaction skipped: reduction < 20%% "
                    "(%d -> %d tokens)",
                    old_tokens, new_tokens,
                )
                return

            # Replace session history
            session.replace_history(new_messages)
            self._compaction_count += 1

            logger.info(
                "Compaction #%d complete: %d tokens -> %d tokens "
                "(%d messages -> %d messages)",
                self._compaction_count,
                sum(len(str(m.get("content", ""))) // 4 for m in conversation),
                session.estimated_tokens,
                len(conversation),
                len(new_messages),
            )

        except Exception:
            logger.warning(
                "Compaction failed at turn %d; continuing without compaction",
                turn, exc_info=True,
            )

    @property
    def compaction_count(self) -> int:
        """Number of compactions performed."""
        return self._compaction_count


class PersistenceHook:
    """Save session to disk after each turn.

    Wraps SessionStore. On each turn_end, persists the current session
    state. Uses SessionStore's append_message for incremental writes
    (new messages only) to avoid rewriting the entire file each turn.

    Exception handling: I/O errors → log.warning + continue.
    Persistence failure does not block the Agent.
    """

    def __init__(self, store: "SessionStore", cwd: str):
        self._store = store
        self._cwd = cwd
        self._session_id: str | None = None
        self._last_offset: int = 0
        self._initialized: bool = False

    async def on_session_start(self, session: Session) -> None:
        """Initialize session on first turn."""
        if self._initialized:
            return

        try:
            self._session_id = getattr(session, '_session_id', None)
            if not self._session_id:
                self._session_id = _generate_session_id()
                session._session_id = self._session_id

            # Get model from system prompt or default
            sp = session.get_system_prompt() or ""
            model_id = "unknown"
            # Try to extract model from system prompt
            if "claude" in sp.lower():
                model_id = "claude"

            meta = SessionMeta(
                session_id=self._session_id,
                cwd=self._cwd,
                model_id=model_id,
                prompt_count=session.prompt_count,
                total_tokens=session.estimated_tokens,
            )
            await self._store.init_session(meta)
            self._last_offset = await self._store.get_history_byte_offset(
                self._session_id, self._cwd,
            )
            self._initialized = True

        except Exception:
            logger.warning("Session init failed", exc_info=True)

    async def on_turn_end(self, session: Session, turn: int) -> None:
        """Persist session state after each turn."""
        if not self._session_id:
            return

        try:
            all_dicts = session.to_api_messages()

            # [2026-08-05] After compaction the in-memory history was replaced.
            # Appending would leave the old pre-compaction history in the file
            # mixed with the compacted one, so rewrite the whole JSONL instead.
            if getattr(session, "_history_replaced", False):
                await self._store.save_full_history(
                    self._session_id, self._cwd, all_dicts,
                )
                session._history_replaced = False
            else:
                current_count = len(all_dicts)
                msg_count = await self._count_persisted()

                # Find what's new since the last persist (delta append).
                new_msgs = all_dicts if msg_count >= current_count else all_dicts[msg_count:]
                for msg in new_msgs:
                    await self._store.append_message(
                        self._session_id, self._cwd, msg,
                    )

            # Update metadata
            await self._store.update_meta(
                self._session_id, self._cwd,
                {
                    "prompt_count": session.prompt_count,
                    "total_tokens": session.estimated_tokens,
                },
            )

            self._last_offset = await self._store.get_history_byte_offset(
                self._session_id, self._cwd,
            )

        except Exception:
            logger.warning("Session persistence failed", exc_info=True)

    async def on_session_end(
        self, session: Session, reason: str = "completed",
    ) -> None:
        """Persist a compacted history even when the run ends without a final
        text turn (max_turns / error). The replaced history must not be lost.

        The normal incremental append already happened in on_turn_end; this
        only handles the compaction-rewrite case for abnormal termination.
        """
        if not self._session_id:
            return
        try:
            if getattr(session, "_history_replaced", False):
                await self._store.save_full_history(
                    self._session_id, self._cwd, session.to_api_messages(),
                )
                session._history_replaced = False
                await self._store.update_meta(
                    self._session_id, self._cwd,
                    {
                        "prompt_count": session.prompt_count,
                        "total_tokens": session.estimated_tokens,
                    },
                )
                self._last_offset = await self._store.get_history_byte_offset(
                    self._session_id, self._cwd,
                )
        except Exception:
            logger.warning(
                "Session end persistence failed", exc_info=True,
            )

    async def _count_persisted(self) -> int:
        """Count messages already persisted."""
        if not self._session_id:
            return 0
        try:
            _, msgs = await self._store.load_session(
                self._session_id, self._cwd,
            )
            return len(msgs)
        except Exception:
            return 0


class EditTrackerHook:
    """Track files edited by the agent.

    Tracks file_path arguments from file_edit and file_write tool calls.
    Does NOT track shell_exec (sed/tee/redirect side-effects are unreliable).

    Design: false-positive tracking (e.g., ls > file) is more dangerous
    than missing some shell-based edits. Agent edits via file_edit
    and file_write cover the vast majority of cases.
    """

    def __init__(self):
        self._edited_files: set[str] = set()

    async def on_tool_result(
        self, tool_name: str, arguments: dict, result: ToolResult
    ) -> None:
        """Record file edits from tool results."""
        if result.is_error:
            return  # Failed edits don't count

        if tool_name in ("file_edit", "file_write"):
            file_path = arguments.get("file_path")
            if file_path:
                self._edited_files.add(str(Path(file_path).resolve()))

    def get_edited_files(self) -> list[str]:
        """Return sorted list of edited file paths."""
        return sorted(self._edited_files)

    def clear(self) -> None:
        """Clear tracked edits (e.g., after compaction or new session)."""
        self._edited_files.clear()


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _estimate_messages(messages: list["Message"]) -> int:
    """Estimate tokens for a list of internal Message objects.

    Used by the compaction Reduction Guard to decide whether a compressed
    history actually shrinks the conversation enough to be worth keeping.
    """
    total = 0
    for m in messages:
        if isinstance(m, SystemMessage):
            total += estimate_tokens(m.content)
        elif isinstance(m, UserMessage):
            total += estimate_tokens(m.content)
        elif isinstance(m, AssistantMessage):
            if m.content:
                total += estimate_tokens(m.content)
            if m.tool_calls:
                for tc in m.tool_calls:
                    total += estimate_tokens(tc.name + tc.arguments)
        elif isinstance(m, ToolResultMessage):
            total += estimate_tokens(m.content)
    return total
