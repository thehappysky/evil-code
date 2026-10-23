"""Adapters bridging ``code_agent`` subsystems to the Evil Code TUI protocols.

The reference TUI speaks a normalized ``AgentEvent`` stream and a set of
controller protocols. These adapters translate the current ``code_agent``
architecture (``Agent``/``Session``/``PermissionManager``/``PlanModeTracker``/
``SessionStore``) into that interface without modifying ``Agent`` source code:
the agent is subclassed for event instrumentation only (open-closed principle),
and the TUI runner gates tool permissions through a ``SessionApprovalBroker``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Callable

from code_agent.core.agent import Agent
from code_agent.core.messages import (
    AssistantMessage as CaAssistantMessage,
)
from code_agent.core.messages import (
    Message as CaMessage,
)
from code_agent.core.messages import (
    SystemMessage as CaSystemMessage,
)
from code_agent.core.messages import (
    ToolResultMessage as CaToolResultMessage,
)
from code_agent.core.messages import (
    UserMessage as CaUserMessage,
)
from code_agent.core.session import Session as CaSession
from code_agent.permission.manager import Decision, PermissionManager
from code_agent.tools.result import ToolResult
from code_agent.tui.domain import (
    AgentEvent,
    AgentEventKind,
    AgentRunResult,
    ApprovalHandler,
    EventSink,
    InteractionMode,
    InteractionModeSelectionResult,
    Message,
    ProviderOption,
    ProviderSelectionResult,
    ReasoningEffort,
    ReasoningEffortSelectionResult,
    Role,
    SessionApprovalBroker,
    SessionItem,
    SessionOption,
    SessionSelectionResult,
    SessionSummary,
    ToolCall,
    build_permission_request,
)

logger = logging.getLogger(__name__)


# ─── Conversion helpers ───────────────────────────────────────────────────────


def _tool_call_arguments(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def to_domain_items(messages: list[CaMessage]) -> tuple[SessionItem, ...]:
    """Convert ``code_agent`` session messages into TUI domain items."""
    items: list[SessionItem] = []
    for message in messages:
        if isinstance(message, CaSystemMessage):
            items.append(Message(Role.SYSTEM, message.content))
        elif isinstance(message, CaUserMessage):
            items.append(Message(Role.USER, message.content))
        elif isinstance(message, CaAssistantMessage):
            calls = tuple(
                ToolCall(
                    id=call.id,
                    name=call.name,
                    arguments=_tool_call_arguments(call.arguments),
                )
                for call in (message.tool_calls or ())
            )
            items.append(Message(Role.ASSISTANT, message.content or "", tool_calls=calls))
        elif isinstance(message, CaToolResultMessage):
            items.append(Message(Role.TOOL, message.content, tool_call_id=message.tool_call_id))
    return tuple(items)


def assistant_response_text(session: CaSession) -> str:
    """Concatenate assistant text from the session (the running response)."""
    parts = [
        message.content
        for message in session.messages
        if isinstance(message, CaAssistantMessage) and message.content
    ]
    return "\n".join(parts)


# ─── Instrumented agent ───────────────────────────────────────────────────────


class InstrumentedAgent(Agent):
    """``Agent`` subclass that emits normalized TUI events for one turn.

    ``Agent`` source is untouched (open-closed principle): this subclass only
    adds event emission around the existing tool-execution boundary and gates
    permissions through the TUI approval broker in place of ``PermissionHook``.
    """

    def __init__(
        self,
        *,
        session: CaSession,
        client: Any,
        tools: Any,
        max_turns: int,
        hooks: list[Any],
        trace: Any = None,
        permission_manager: PermissionManager | None = None,
        approval_broker: SessionApprovalBroker | None = None,
        session_id: str | None = None,
        provider_name: str = "code-agent",
        model_name: str = "unknown",
    ):
        super().__init__(
            session, client, tools,
            max_turns=max_turns,
            hooks=hooks,
            trace=trace,
        )
        self._permission_manager = permission_manager
        self._broker = approval_broker or SessionApprovalBroker()
        self._session_id = session_id
        self._provider_name = provider_name
        self._model_name = model_name
        self._sink: EventSink | None = None
        self._sequence = 0
        self._collected_events: list[AgentEvent] = []

    @property
    def approval_broker(self) -> SessionApprovalBroker:
        return self._broker

    def set_sink(self, sink: EventSink | None) -> None:
        self._sink = sink
        if sink is None:
            self._sequence = 0

    async def emit(self, kind: AgentEventKind, data: dict[str, object]) -> None:
        """Emit one normalized event to the current sink (if any)."""
        self._sequence += 1
        event = AgentEvent.create(self._sequence, kind, data)
        self._collected_events.append(event)
        if self._sink is None:
            return
        outcome = self._sink(event)
        if inspect.isawaitable(outcome):
            await outcome

    # ── Turn loop instrumentation ─────────────────────────────────────────

    async def run(self, user_input: str):
        """Run one user turn, translating stream events for the TUI sink."""
        await self.emit(AgentEventKind.SESSION_STARTED, {
            "session_id": self._session_id or "",
            "provider": self._provider_name,
            "model": self._model_name,
        })
        await self.emit(AgentEventKind.USER_MESSAGE, {"content": user_input})

        steps = 0
        async for event in super().run(user_input):
            if event.kind == "text_delta" and event.text:
                await self.emit(AgentEventKind.TEXT_DELTA, {"text": event.text})
            elif event.kind == "thinking_delta" and event.text:
                await self.emit(AgentEventKind.REASONING_DELTA, {"text": event.text})
            elif event.kind == "tool_call_delta":
                # TOOL_REQUESTED is emitted at execution time in _execute_one_tool
                # with the fully assembled arguments.
                pass
            elif event.kind == "turn_end":
                steps += 1
                if event.usage:
                    input_tokens = event.usage.get("prompt_tokens", 0) or 0
                    output_tokens = event.usage.get("completion_tokens", 0) or 0
                    await self.emit(AgentEventKind.CONTEXT_USAGE_UPDATED, {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "used_tokens": input_tokens + output_tokens,
                        "estimated": True,
                    })
                await self.emit(AgentEventKind.MODEL_THINKING_COMPLETED, {
                    "step": steps,
                    "duration_seconds": 0.0,
                })
            elif event.kind == "error" and event.text:
                await self.emit(AgentEventKind.TURN_FAILED, {
                    "error_type": "AgentError",
                    "message": event.text,
                    "cancelled": False,
                    "duration_seconds": 0.0,
                })
            yield event

        self._last_steps = steps

    # ── Tool execution instrumentation ─────────────────────────────────────

    async def _execute_one_tool(self, tc, lock=None):
        # [Phase 5] ``lock`` is passed through to the base so same-path write
        # tools stay serialized under the Agent's parallel execution.
        args = _tool_call_arguments(tc.arguments)
        await self.emit(AgentEventKind.TOOL_REQUESTED, {
            "id": tc.id,
            "name": tc.name,
            "arguments": dict(args),
        })

        gate_error: str | None = None
        if self._permission_manager is not None:
            decision = await self._permission_manager.decide(tc.name, args)
            if decision is Decision.DENY:
                gate_error = f"permission denied: {tc.name}"
            elif decision is Decision.ASK:
                reason = (
                    getattr(self._permission_manager, "last_reason", None)
                    or "requires approval"
                )
                request = build_permission_request(
                    tc.id, tc.name, args, reason
                )
                await self.emit(AgentEventKind.TOOL_APPROVAL_REQUESTED, {
                    "id": tc.id,
                    "name": tc.name,
                    "reason": request.reason,
                    "summary": request.summary,
                })
                approval = await self._broker.request(request)
                effect = "allow" if approval.allowed else "deny"
                await self.emit(AgentEventKind.TOOL_APPROVAL_RESOLVED, {
                    "id": tc.id,
                    "name": tc.name,
                    "effect": effect,
                    "outcome": approval.kind.value,
                    "reason": approval.reason,
                })
                if not approval.allowed:
                    gate_error = f"permission denied: {approval.reason}"
            else:  # ALLOW
                allow_reason = (
                    getattr(self._permission_manager, "last_reason", None)
                    or "allowed by permission policy"
                )
                await self.emit(AgentEventKind.TOOL_PERMISSION, {
                    "id": tc.id,
                    "name": tc.name,
                    "effect": "allow",
                    "reason": allow_reason,
                })

        if gate_error is not None:
            await self.emit(AgentEventKind.TOOL_FAILED, {
                "id": tc.id,
                "name": tc.name,
                "output": gate_error,
                "is_error": True,
                "duration_seconds": 0.0,
            })
            return ToolResult.error(gate_error)

        started_at = monotonic()
        await self.emit(AgentEventKind.TOOL_STARTED, {"id": tc.id, "name": tc.name})
        result = await super()._execute_one_tool(tc, lock=lock)
        duration = monotonic() - started_at
        kind = AgentEventKind.TOOL_FAILED if result.is_error else AgentEventKind.TOOL_COMPLETED
        await self.emit(kind, {
            "id": tc.id,
            "name": tc.name,
            "output": result.output,
            "is_error": result.is_error,
            "duration_seconds": duration,
        })
        return result


# ─── Runner ───────────────────────────────────────────────────────────────────


class TuiAgentRunner:
    """``ConversationRunner`` over an instrumented ``code_agent`` agent."""

    def __init__(
        self,
        agent: InstrumentedAgent,
        *,
        session_id: str | None = None,
    ):
        self._agent = agent
        self._session_id = session_id

    @property
    def agent(self) -> InstrumentedAgent:
        return self._agent

    @property
    def session(self) -> CaSession:
        return self._agent._session

    @property
    def session_id(self) -> str | None:
        # [2026-08-04] Prefer the live session id (PersistenceHook assigns it
        # on the first turn); fall back to the id captured at binding time so
        # /rename and /status work for brand-new sessions too.
        live = getattr(self._agent._session, "_session_id", None)
        return live or self._session_id

    @property
    def items(self) -> tuple[SessionItem, ...]:
        return to_domain_items(self.session.messages)

    async def run(self, prompt: str, *, sink: EventSink | None = None) -> AgentRunResult:
        self._agent.set_sink(sink)
        started_at = monotonic()
        steps = 0
        text_parts: list[str] = []
        try:
            async for event in self._agent.run(prompt):
                if event.kind == "text_delta" and event.text:
                    text_parts.append(event.text)
            # InstrumentedAgent only records _last_steps after its async
            # generator has been fully drained, so reading it mid-stream always
            # yields 0. Read it now that the loop has finished.
            steps = getattr(self._agent, "_last_steps", steps)
            await self._agent.emit(AgentEventKind.TURN_COMPLETED, {
                "step": steps,
                "stop_reason": "end_turn",
                "input_tokens": None,
                "output_tokens": None,
                "duration_seconds": monotonic() - started_at,
            })
        except asyncio.CancelledError:
            await self._agent.emit(AgentEventKind.TURN_FAILED, {
                "error_type": "CancelledError",
                "message": "turn cancelled",
                "cancelled": True,
                "duration_seconds": monotonic() - started_at,
            })
            raise
        except Exception as error:
            await self._agent.emit(AgentEventKind.TURN_FAILED, {
                "error_type": type(error).__name__,
                "message": str(error),
                "cancelled": False,
                "duration_seconds": monotonic() - started_at,
            })
            raise
        finally:
            self._agent.set_sink(None)

        items = self.items
        messages = tuple(item for item in items if isinstance(item, Message))
        response = "".join(text_parts) or assistant_response_text(self.session)
        return AgentRunResult(
            session_id=self._session_id,
            response=response,
            messages=messages,
            items=items,
            events=tuple(self._agent._collected_events),
            steps=max(1, steps),
        )


# ─── Reasoning effort controller ──────────────────────────────────────────────


class ReasoningEffortController:
    """UI-level reasoning effort; persisted via ``UiPreferencesStore``."""

    def __init__(
        self,
        ui_preferences: Any | None = None,
        initial: ReasoningEffort = ReasoningEffort.HIGH,
    ):
        self._ui_preferences = ui_preferences
        self._effort = initial
        self._effective = initial.effective

    @property
    def reasoning_effort(self) -> ReasoningEffort:
        return self._effort

    @property
    def effective_reasoning_effort(self) -> ReasoningEffort:
        return self._effective

    async def set_reasoning_effort(
        self,
        effort: ReasoningEffort,
    ) -> ReasoningEffortSelectionResult:
        if effort is self._effort:
            return ReasoningEffortSelectionResult(
                effort, effort.effective, changed=False,
            )
        self._effort = effort
        self._effective = effort.effective
        if self._ui_preferences is not None:
            try:
                await self._ui_preferences.save_reasoning_effort(effort)
            except Exception:
                logger.warning("Failed to persist reasoning effort", exc_info=True)
        return ReasoningEffortSelectionResult(
            effort, effort.effective, changed=True,
        )


# ─── Interaction mode controller ──────────────────────────────────────────────


class InteractionModeController:
    """Maps TUI interaction modes onto code_agent plan mode + YOLO permission."""

    def __init__(
        self,
        *,
        plan_tracker: Any | None = None,
        permission_manager: PermissionManager | None = None,
        ui_preferences: Any | None = None,
        initial: InteractionMode = InteractionMode.NORMAL,
        auto_unrestricted: bool = False,
    ):
        self._plan_tracker = plan_tracker
        self._permission_manager = permission_manager
        self._ui_preferences = ui_preferences
        self._mode = initial
        self._auto_unrestricted = auto_unrestricted

    @property
    def interaction_mode(self) -> InteractionMode:
        return self._mode

    @property
    def auto_mode_unrestricted(self) -> bool:
        return self._auto_unrestricted

    def _apply_mode_to_backends(self, mode: InteractionMode) -> None:
        """Mirror the interaction mode onto PlanModeTracker + PermissionManager.

        Idempotent: safe to call for the currently-selected mode too (e.g.
        when the mode is restored from persisted prefs at startup, the
        tracker starts INACTIVE and YOLO is unset even though the UI shows
        plan/auto — this closes that gap).
        """
        if self._plan_tracker is not None:
            if mode is InteractionMode.PLAN:
                if self._plan_tracker.state.value in ("inactive", "pending"):
                    self._plan_tracker.activate()
            else:
                if self._plan_tracker.state.value in ("active", "pending", "exit_pending"):
                    self._plan_tracker.deactivate()
        if self._permission_manager is not None:
            self._permission_manager.set_yolo(mode is InteractionMode.AUTO)

    async def set_interaction_mode(
        self,
        mode: InteractionMode,
    ) -> InteractionModeSelectionResult:
        if mode is self._mode:
            # Even on a no-op selection, make sure the backend state matches
            # (mode may have been restored from persisted prefs at startup).
            self._apply_mode_to_backends(mode)
            return InteractionModeSelectionResult(
                mode, changed=False, auto_unrestricted=self._auto_unrestricted,
            )
        self._mode = mode
        self._apply_mode_to_backends(mode)
        if self._ui_preferences is not None:
            try:
                await self._ui_preferences.save_interaction_mode(mode)
            except Exception:
                logger.warning("Failed to persist interaction mode", exc_info=True)
        return InteractionModeSelectionResult(
            mode, changed=True, auto_unrestricted=self._auto_unrestricted,
        )


# ─── Runner binding factory result ────────────────────────────────────────────


@dataclass
class RunnerBinding:
    runner: TuiAgentRunner
    items: tuple[SessionItem, ...] = ()


ProviderResolver = Callable[[str], Any]  # async: profile name -> RunnerBinding
SessionResolver = Callable[[str], Any]  # async: session id -> RunnerBinding
SessionCatalog = Callable[[], Any]  # async: -> list[SessionSummary]
SessionRenamer = Callable[[str, str], Any]  # async: (session_id, title) -> SessionSummary


# ─── Composite controller ─────────────────────────────────────────────────────


class TuiController:
    """Serializes turns and swaps the conversation at a safe provider/session boundary.

    This single object satisfies every protocol ``EvilCodeApp`` accepts:
    ``ConversationRunner``, ``ProviderController``, ``ReasoningController``,
    ``InteractionModeController`` and ``SessionController``.
    """

    def __init__(
        self,
        *,
        runner: TuiAgentRunner,
        reasoning: ReasoningEffortController,
        interaction_mode: InteractionModeController,
        provider_store: Any | None = None,
        managed_settings: Any | None = None,
        selected_profile: str = "default",
        provider_name: str = "code-agent",
        model_name: str = "unknown",
        resolve_provider: ProviderResolver | None = None,
        list_sessions: SessionCatalog | None = None,
        rename_session: SessionRenamer | None = None,
        resolve_session: SessionResolver | None = None,
    ):
        self._runner = runner
        self._reasoning = reasoning
        self._interaction_mode = interaction_mode
        self._provider_store = provider_store
        self._managed_settings = managed_settings
        self._selected_profile = selected_profile
        self._provider_name = provider_name
        self._model_name = model_name
        self._resolve_provider = resolve_provider
        self._list_sessions = list_sessions
        self._rename_session = rename_session
        self._resolve_session = resolve_session

    # ── ConversationRunner ───────────────────────────────────────────────

    @property
    def session_id(self) -> str | None:
        return self._runner.session_id

    async def run(self, prompt: str, *, sink: EventSink | None = None) -> AgentRunResult:
        return await self._runner.run(prompt, sink=sink)

    # ── ProviderController ───────────────────────────────────────────────

    @property
    def profiles(self) -> tuple[ProviderOption, ...]:
        options: list[ProviderOption] = []
        settings = self._managed_settings
        if settings is not None:
            default = settings.default_provider
            for profile in settings.profiles:
                options.append(ProviderOption(
                    name=profile.name,
                    protocol=profile.protocol,
                    model=profile.model,
                    available=True,
                    credential_configured=profile.api_key is not None,
                    default=profile.name == default,
                    selected=profile.name == self._selected_profile,
                ))
        return tuple(options)

    @property
    def selected_profile(self) -> str:
        return self._selected_profile

    async def select_profile(self, name: str) -> ProviderSelectionResult:
        if name == self._selected_profile:
            return ProviderSelectionResult(
                name, self._provider_name, self._model_name,
                previous_session_id=None, changed=False,
            )
        if self._managed_settings is None or self._managed_settings.profile(name) is None:
            return ProviderSelectionResult(
                name, self._provider_name, self._model_name,
                previous_session_id=None, changed=False,
            )
        if self._provider_store is not None:
            try:
                self._managed_settings = await self._provider_store.set_default(name)
            except Exception:
                logger.warning("Failed to persist provider default", exc_info=True)
        previous = self._runner.session_id
        if self._resolve_provider is not None:
            binding = await self._resolve_provider(name)
            self._runner = binding.runner
        self._selected_profile = name
        profile = self._managed_settings.profile(name)
        provider_name = profile.protocol if profile is not None else self._provider_name
        model_name = profile.model if profile is not None else self._model_name
        return ProviderSelectionResult(
            name, provider_name, model_name,
            previous_session_id=previous, changed=True,
        )

    # ── ReasoningController ──────────────────────────────────────────────

    @property
    def reasoning_effort(self) -> ReasoningEffort:
        return self._reasoning.reasoning_effort

    @property
    def effective_reasoning_effort(self) -> ReasoningEffort:
        return self._reasoning.effective_reasoning_effort

    async def set_reasoning_effort(
        self,
        effort: ReasoningEffort,
    ) -> ReasoningEffortSelectionResult:
        return await self._reasoning.set_reasoning_effort(effort)

    # ── InteractionModeController ────────────────────────────────────────

    @property
    def interaction_mode(self) -> InteractionMode:
        return self._interaction_mode.interaction_mode

    @property
    def auto_mode_unrestricted(self) -> bool:
        return self._interaction_mode.auto_mode_unrestricted

    async def set_interaction_mode(
        self,
        mode: InteractionMode,
    ) -> InteractionModeSelectionResult:
        return await self._interaction_mode.set_interaction_mode(mode)

    # ── SessionController ────────────────────────────────────────────────

    async def list_sessions(self, query: str | None = None) -> tuple[SessionOption, ...]:
        if self._list_sessions is None:
            return ()
        try:
            summaries = await self._list_sessions()
        except Exception:
            logger.warning("Failed to list sessions", exc_info=True)
            return ()
        if query:
            normalized = query.casefold()
            summaries = tuple(
                summary for summary in summaries
                if normalized in (summary.title or "").casefold()
                or normalized in summary.id.casefold()
            )
        current = self._runner.session_id
        return tuple(
            SessionOption(
                session_id=summary.id,
                source_provider=summary.provider,
                source_model=summary.model,
                updated_at=summary.updated_at,
                resume_profile=self._selected_profile,
                current=summary.id == current,
                source_profile_match=True,
                selectable=True,
                title=summary.title,
            )
            for summary in summaries
        )

    async def select_session(self, session_id: str) -> SessionSelectionResult:
        if self._resolve_session is None:
            return SessionSelectionResult(
                session_id=session_id,
                source_provider=self._provider_name,
                source_model=self._model_name,
                profile_name=self._selected_profile,
                provider_name=self._provider_name,
                model_name=self._model_name,
                previous_session_id=None,
                changed=False,
                source_profile_match=True,
                items=(),
            )
        try:
            binding = await self._resolve_session(session_id)
        except Exception as error:
            raise ValueError(f"failed to resume session: {error}") from error
        previous = self._runner.session_id
        self._runner = binding.runner
        return SessionSelectionResult(
            session_id=session_id,
            source_provider=self._provider_name,
            source_model=self._model_name,
            profile_name=self._selected_profile,
            provider_name=self._provider_name,
            model_name=self._model_name,
            previous_session_id=previous,
            changed=True,
            source_profile_match=True,
            items=binding.items,
        )

    async def rename_session(self, title: str) -> SessionSummary:
        if self._rename_session is None or self._runner.session_id is None:
            raise ValueError("interactive session rename is unavailable")
        return await self._rename_session(self._runner.session_id, title)


__all__ = [
    "Decision",
    "InstrumentedAgent",
    "InteractionModeController",
    "ReasoningEffortController",
    "RunnerBinding",
    "TuiAgentRunner",
    "TuiController",
    "to_domain_items",
]
