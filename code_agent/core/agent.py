"""Agent main loop + AgentHook middleware pipeline.

Logic reference: Rust version SessionActor::process_conversation_turn()
(turn.rs:1695-2300).

This is the most critical file in the system. It implements:
- think -> act -> observe cycle
- AgentHook protocol (defined in core/hooks.py, implemented in Phase 2-4)
- AsyncIterator streaming (CLI and TUI both consume the same Agent)
- Defensive hook exception handling (two-layer strategy)

Design principles:
- Open-Closed: extend via hooks, never modify Agent code
- Dependency injection: all external deps via constructor
- Tool definitions fetched every turn (MCP tools available next turn)
- max_turns hard limit prevents infinite loops
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from code_agent.api.client import ChatClient
from code_agent.api.schemas import StreamEvent
from code_agent.core.exceptions import MaxTurnsReached, ToolNotFoundError
from code_agent.core.hooks import AgentHook, HookDecision
from code_agent.core.messages import ToolCallItem
from code_agent.core.session import Session
from code_agent.telemetry.tracer import TraceWriter
from code_agent.tools.registry import ToolRegistry
from code_agent.tools.result import ToolResult

logger = logging.getLogger(__name__)


# ─── Parallel tool execution helpers (Phase 5) ────────────────────────────────


# Tools that mutate the workspace; same-path calls are serialized with a lock.
_WRITE_TOOL_NAMES = frozenset({"file_edit", "file_write", "file_delete"})
_PATH_KEYS = ("file_path", "path", "target_file")


def _tool_lock_key(tc: "ToolCallItem") -> str | None:
    """Return the normalized target path for a write tool, else None.

    Mirrors Rust ``lock_path_for_args`` (tool_dispatch.rs): read/exec tools
    return None (fully concurrent); same-path write tools share a lock and
    are serialized to avoid TOCTOU on read-modify-write.
    """
    if tc.name not in _WRITE_TOOL_NAMES:
        return None
    try:
        args = json.loads(tc.arguments) if tc.arguments else {}
    except json.JSONDecodeError:
        return None
    for key in _PATH_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            try:
                return str(Path(value).expanduser().resolve())
            except OSError:
                return None
    return None


# ─── Agent ─────────────────────────────────────────────────────────────────────


class Agent:
    """AI Coding Agent. think -> act -> observe main loop.

    All external dependencies injected via constructor (dependency inversion):
    - session: Conversation state (same layer)
    - client: LLM communication (injected, lazy init + auto token refresh)
    - tools: Tool registry (injected)
    - max_turns: Maximum reasoning turns per prompt
    - hooks: AgentHook list (injected, optional) — THE ONLY extension point.
      Phase 2-4 extend via this parameter. Agent source code never changes.
    """

    def __init__(
        self,
        session: Session,
        client: ChatClient,
        tools: ToolRegistry,
        *,
        max_turns: int = 20,
        hooks: list[AgentHook] | None = None,
        trace: "TraceWriter | None" = None,
    ):
        self._session = session
        self._client = client
        self._tools = tools
        self._max_turns = max_turns
        self._hooks = hooks or []
        self._trace = trace

    async def run(self, user_input: str) -> AsyncIterator[StreamEvent]:
        """Process user input through the think -> act -> observe loop.

        This is an async generator that yields StreamEvents.
        CLI prints text_delta to stdout; TUI renders to widgets.
        Both consume the same Agent output.

        Args:
            user_input: The user's message.

        Yields:
            StreamEvent instances (text_delta, tool_call_delta, thinking_delta, turn_end).

        Raises:
            MaxTurnsReached: If reasoning exceeds max_turns limit.
        """
        self._session.add_user_message(user_input)

        # Each run() is one "session" for the hook lifecycle: reset the
        # exactly-once guard so on_session_end fires once per run.
        self._session_end_fired = False

        if self._trace:
            self._trace.event("user_input", text=user_input[:500])

        # Hook: on_session_start
        for hook in self._hooks:
            if not hasattr(hook, "on_session_start"):
                continue
            try:
                await hook.on_session_start(self._session)  # type: ignore[attr-defined]
            except Exception:
                logger.warning(
                    "Hook %s.on_session_start failed",
                    type(hook).__name__, exc_info=True,
                )

        reason = "completed"
        try:
            for turn in range(self._max_turns):
                # Fetch tool definitions every turn (MCP tools available next turn)
                tools = self._tools.get_definitions()

                if self._trace:
                    self._trace.event("turn_start", turn=turn, tool_count=len(tools))

                # Hook: on_turn_start
                for hook in self._hooks:
                    if not hasattr(hook, "on_turn_start"):
                        continue
                    try:
                        await hook.on_turn_start(self._session, turn, tools)  # type: ignore[attr-defined]
                    except Exception:
                        logger.warning(
                            "Hook %s.on_turn_start failed",
                            type(hook).__name__, exc_info=True,
                        )

                messages = self._session.to_api_messages()
                pending_calls: dict[int, ToolCallItem] = {}
                text_parts: list[str] = []

                if self._trace:
                    self._trace.event("api_request", messages_count=len(messages), tool_count=len(tools))

                async for event in self._client.chat_stream(messages, tools, trace=self._trace):
                    yield event

                    # Accumulate streamed text so it can be persisted to the session.
                    if event.kind == "text_delta" and event.text:
                        text_parts.append(event.text)

                    # Accumulate streaming tool_call deltas by index
                    if event.kind == "tool_call_delta" and event.tool_call:
                        idx = event.tool_call.index
                        if idx not in pending_calls:
                            pending_calls[idx] = ToolCallItem(
                                id=event.tool_call.id or "",
                                name=event.tool_call.name or "",
                                arguments="",
                            )
                        tc = pending_calls[idx]
                        if event.tool_call.id:
                            tc.id = event.tool_call.id
                        if event.tool_call.name:
                            tc.name = event.tool_call.name
                        if event.tool_call.arguments_fragment:
                            tc.arguments += event.tool_call.arguments_fragment

                    elif event.kind == "turn_end":
                        # Finalize tool calls from accumulated deltas. Persist the
                        # streamed text too — a pure text response must not leave an
                        # assistant message with content=None (it would be lost from
                        # the session/transcript).
                        if event.usage:
                            # Record real API usage into the session ledger.
                            self._session.record_usage(
                                event.usage, self._client.model,
                            )
                        tool_calls = list(pending_calls.values()) if pending_calls else None
                        text = "".join(text_parts)
                        self._session.add_assistant_message(
                            text=text or None, tool_calls=tool_calls,
                        )

                        if tool_calls:
                            if self._trace:
                                self._trace.event("turn_end", kind="tool_calls", count=len(tool_calls))
                            # Execute tools in parallel (same-path writes serialized
                            # by a per-path lock), preserving model output order.
                            locks: dict[str, asyncio.Lock] = {}
                            for tc in tool_calls:
                                key = _tool_lock_key(tc)
                                if key is not None:
                                    locks.setdefault(key, asyncio.Lock())

                            results = await asyncio.gather(
                                *(
                                    self._execute_one_tool(
                                        tc, lock=locks.get(_tool_lock_key(tc)),
                                    )
                                    for tc in tool_calls
                                ),
                                return_exceptions=True,
                            )

                            # Append results in the model's original order; a
                            # single tool failing must not break the batch.
                            for tc, result in zip(tool_calls, results):
                                if isinstance(result, BaseException):
                                    logger.exception(
                                        "Tool '%s' unexpected error", tc.name,
                                    )
                                    result = ToolResult.error(
                                        f"Execution failed: {result}",
                                    )
                                self._session.add_tool_result(
                                    call_id=tc.id,
                                    content=result.output,
                                    is_error=result.is_error,
                                )
                            break  # Exit stream loop, enter next turn
                        else:
                            # Pure text response — turn complete
                            if self._trace:
                                self._trace.event("turn_end", kind="text")
                            for hook in self._hooks:
                                if not hasattr(hook, "on_turn_end"):
                                    continue
                                try:
                                    await hook.on_turn_end(self._session, turn)  # type: ignore[attr-defined]
                                except Exception:
                                    logger.warning(
                                        "Hook %s.on_turn_end failed",
                                        type(hook).__name__, exc_info=True,
                                    )
                            return  # Done

            raise MaxTurnsReached(self._max_turns)
        except MaxTurnsReached:
            reason = "max_turns"
            raise
        except Exception:
            reason = "error"
            raise
        finally:
            # Exactly-once session-end hook (idempotent — also fires when the
            # generator is closed early via aclose()/GeneratorExit).
            if not getattr(self, "_session_end_fired", False):
                self._session_end_fired = True
                for hook in self._hooks:
                    if not hasattr(hook, "on_session_end"):
                        continue
                    try:
                        await hook.on_session_end(self._session, reason=reason)  # type: ignore[attr-defined]
                    except Exception:
                        logger.warning(
                            "Hook %s.on_session_end failed",
                            type(hook).__name__, exc_info=True,
                        )

    async def _execute_one_tool(
        self, tc: ToolCallItem, lock: asyncio.Lock | None = None,
    ) -> ToolResult:
        """Execute a single tool call with hook pipeline.

        Hook chain: each hook's on_tool_call is called in registration order.
        MODIFY hooks modify args dict in-place — subsequent hooks see modified args.
        Order-sensitive: registration order determines modification priority.

        Args:
            tc: ToolCallItem with id, name, arguments (raw JSON string).
            lock: Optional per-path lock; when present the actual tool
                execution is held under it so same-path writes are serialized
                (parallel tool calls, Phase 5).

        Returns:
            ToolResult from execution or error.
        """
        # Parse arguments
        try:
            args = json.loads(tc.arguments) if tc.arguments else {}
        except json.JSONDecodeError:
            return ToolResult.error(
                f"Arguments are not valid JSON: {tc.arguments[:200]}"
            )

        # Hook pipeline: on_tool_call (with defensive exception handling)
        for hook in self._hooks:
            if not hasattr(hook, "on_tool_call"):
                continue
            try:
                decision = await hook.on_tool_call(tc.name, args)  # type: ignore[attr-defined]
            except Exception:
                logger.warning(
                    "Hook %s.on_tool_call exception — treating as DENY",
                    type(hook).__name__, exc_info=True,
                )
                decision = HookDecision.DENY  # fail-safe fallback

            if decision == HookDecision.DENY:
                return ToolResult.error(f"Permission denied: {tc.name}")
            if decision == HookDecision.MODIFY:
                # Hook modified args in-place via reference.
                # Subsequent hooks see the modified args.
                pass

        if self._trace:
            self._trace.event("tool_call", tool=tc.name, args=args)

        # Execute tool (held under the per-path lock when provided)
        try:
            if lock is not None:
                async with lock:
                    result = await self._tools.execute(tc.name, args)
            else:
                result = await self._tools.execute(tc.name, args)
        except ToolNotFoundError:
            return ToolResult.error(f"Unknown tool '{tc.name}'")
        except Exception as exc:
            logger.exception(f"Tool '{tc.name}' unexpected error")
            return ToolResult.error(f"Execution failed: {exc}")

        if self._trace:
            self._trace.event("tool_result", tool=tc.name,
                              output=result.output[:500],
                              is_error=result.is_error,
                              truncated=result.truncated)

        # Hook pipeline: on_tool_result (with defensive exception handling)
        for hook in self._hooks:
            if not hasattr(hook, "on_tool_result"):
                continue
            try:
                await hook.on_tool_result(tc.name, args, result)  # type: ignore[attr-defined]
            except Exception:
                logger.warning(
                    "Hook %s.on_tool_result failed",
                    type(hook).__name__, exc_info=True,
                )

        return result
