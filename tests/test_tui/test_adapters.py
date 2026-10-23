# [TUI] 2026-08-02: Tests for code_agent/tui/adapters.py — real Agent bridge.
"""Tests for ``code_agent/tui/adapters.py``.

Covers:
- ``InstrumentedAgent.run()`` event emission for text and tool-call turns
- Permission gating through ``PermissionManager`` + ``SessionApprovalBroker``
- ``TuiAgentRunner.run()`` driving a real ``Agent`` into an ``AgentRunResult``
- ``SessionApprovalBroker`` allow/deny/reuse/headless behavior
- ``build_permission_request`` summaries + hashed scope keys
- ``to_domain_items`` message conversion

These tests build a *real* ``Agent`` (via the ``InstrumentedAgent`` subclass)
against a ``MockChatClient`` and a real ``ToolRegistry`` echo tool, mirroring
``tests/test_core/test_agent.py``.
"""

from __future__ import annotations

from code_agent.core.messages import ToolCallItem
from code_agent.core.session import Session
from code_agent.permission.manager import PermissionManager
from code_agent.tools.registry import ToolRegistry
from code_agent.tools.result import ToolResult
from code_agent.tui.adapters import (
    InstrumentedAgent,
    InteractionModeController,
    TuiAgentRunner,
    to_domain_items,
)
from code_agent.tui.domain import (
    AgentEvent,
    AgentEventKind,
    InteractionMode,
    Message,
    PermissionApproval,
    PermissionApprovalKind,
    PermissionRequest,
    Role,
    SessionApprovalBroker,
    build_permission_request,
)

from conftest import MockChatClient, make_text_response, make_tool_call_response


# ─── Helpers ───────────────────────────────────────────────────────────────────


class RecordingSink:
    """EventSink that records every pushed AgentEvent."""

    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    def __call__(self, event: AgentEvent) -> None:
        self.events.append(event)


def _kinds(events: list[AgentEvent]) -> list[AgentEventKind]:
    return [event.kind for event in events]


def _build_instrumented_agent(
    responses: list[list],
    *,
    max_turns: int = 20,
    permission_manager: PermissionManager | None = None,
    approval_broker: SessionApprovalBroker | None = None,
) -> tuple[InstrumentedAgent, MockChatClient]:
    """Build an InstrumentedAgent with a mock client and a real echo tool."""
    from pydantic import BaseModel, Field

    client = MockChatClient()
    client.set_responses(responses)

    session = Session(system_prompt="Test agent.")

    registry = ToolRegistry()

    class EchoInput(BaseModel):
        text: str = Field(default="")

    @registry.register("echo", EchoInput, description="Echo tool")
    async def echo(input: EchoInput) -> ToolResult:
        return ToolResult.ok(f"Echo: {input.text}")

    registry.finalize()

    agent = InstrumentedAgent(
        session=session,
        client=client,
        tools=registry,
        max_turns=max_turns,
        hooks=[],
        permission_manager=permission_manager,
        approval_broker=approval_broker,
        session_id="test-session",
        provider_name="test-provider",
        model_name="test-model",
    )
    return agent, client


async def _run_agent(
    agent: InstrumentedAgent,
    prompt: str,
    sink: RecordingSink | None = None,
) -> list:
    """Consume ``agent.run()`` to completion with an optional sink attached."""
    if sink is not None:
        agent.set_sink(sink)
    events = []
    async for event in agent.run(prompt):
        events.append(event)
    return events


async def _approve_once(request: PermissionRequest) -> PermissionApproval:
    return PermissionApproval.allow_once("approved by test handler")


class _CountingHandler:
    """ApprovalHandler stub that counts invocations and returns a fixed approval."""

    def __init__(self, approval: PermissionApproval) -> None:
        self.calls = 0
        self._approval = approval

    async def __call__(self, request: PermissionRequest) -> PermissionApproval:
        self.calls += 1
        return self._approval


# ─── InstrumentedAgent: text response ──────────────────────────────────────────


class TestInstrumentedAgentText:
    async def test_text_response_emits_terminal_event_sequence(self):
        """A text turn emits SESSION_STARTED, USER_MESSAGE, TEXT_DELTA, then a terminal event."""
        agent, _ = _build_instrumented_agent([make_text_response("Hello world!")])
        sink = RecordingSink()

        await _run_agent(agent, "Say hi", sink)

        assert _kinds(sink.events) == [
            AgentEventKind.SESSION_STARTED,
            AgentEventKind.USER_MESSAGE,
            AgentEventKind.TEXT_DELTA,
            AgentEventKind.MODEL_THINKING_COMPLETED,
        ]

        text = "".join(
            str(e.data["text"])
            for e in sink.events
            if e.kind == AgentEventKind.TEXT_DELTA
        )
        assert text == "Hello world!"

    async def test_session_started_carries_metadata(self):
        """The SESSION_STARTED event carries session/provider/model metadata."""
        agent, _ = _build_instrumented_agent([make_text_response("hi")])
        sink = RecordingSink()

        await _run_agent(agent, "Say hi", sink)

        started = sink.events[0]
        assert started.kind == AgentEventKind.SESSION_STARTED
        assert started.data["session_id"] == "test-session"
        assert started.data["provider"] == "test-provider"
        assert started.data["model"] == "test-model"

        user = sink.events[1]
        assert user.kind == AgentEventKind.USER_MESSAGE
        assert user.data["content"] == "Say hi"

    async def test_event_sequences_are_monotonic(self):
        """Emitted events are numbered 1..N in emission order."""
        agent, _ = _build_instrumented_agent([make_text_response("hi")])
        sink = RecordingSink()

        await _run_agent(agent, "Say hi", sink)

        sequences = [e.sequence for e in sink.events]
        assert sequences == list(range(1, len(sequences) + 1))


# ─── InstrumentedAgent: tool-call turn ─────────────────────────────────────────


class TestInstrumentedAgentTool:
    async def test_tool_call_executes_through_registry(self):
        """A tool call emits tool events and the tool runs against the real registry."""
        responses = [
            make_tool_call_response("call_1", "echo", '{"text": "hi"}'),
            make_text_response("Done"),
        ]
        agent, client = _build_instrumented_agent(responses)
        sink = RecordingSink()

        await _run_agent(agent, "Run echo", sink)

        kinds = _kinds(sink.events)
        assert AgentEventKind.TOOL_REQUESTED in kinds
        assert AgentEventKind.TOOL_STARTED in kinds
        assert AgentEventKind.TOOL_COMPLETED in kinds
        # No permission manager configured -> no permission events.
        assert AgentEventKind.TOOL_PERMISSION not in kinds
        assert AgentEventKind.TOOL_APPROVAL_REQUESTED not in kinds

        # Relative ordering: requested -> started -> completed.
        positions = {
            kind: kinds.index(kind)
            for kind in (
                AgentEventKind.TOOL_REQUESTED,
                AgentEventKind.TOOL_STARTED,
                AgentEventKind.TOOL_COMPLETED,
            )
        }
        assert (
            positions[AgentEventKind.TOOL_REQUESTED]
            < positions[AgentEventKind.TOOL_STARTED]
            < positions[AgentEventKind.TOOL_COMPLETED]
        )

        # The tool actually executed and the result was recorded.
        completed = next(
            e for e in sink.events if e.kind == AgentEventKind.TOOL_COMPLETED
        )
        assert completed.data["name"] == "echo"
        assert completed.data["output"] == "Echo: hi"
        assert completed.data["is_error"] is False

        # Tool result was fed back to the model on the next turn.
        tool_msgs = [
            m for m in client.call_history[1]["messages"] if m.get("role") == "tool"
        ]
        assert len(tool_msgs) == 1
        assert "Echo: hi" in tool_msgs[0]["content"]

    async def test_permission_allow_emits_permission_event(self):
        """An ALLOW decision emits TOOL_PERMISSION and the tool still executes."""
        responses = [
            make_tool_call_response("call_1", "echo", '{"text": "ok"}'),
            make_text_response("Fine"),
        ]
        permission_manager = PermissionManager(yolo_mode=True)
        agent, _ = _build_instrumented_agent(
            responses, permission_manager=permission_manager
        )
        sink = RecordingSink()

        await _run_agent(agent, "Run echo", sink)

        kinds = _kinds(sink.events)
        assert AgentEventKind.TOOL_PERMISSION in kinds
        permission = next(
            e for e in sink.events if e.kind == AgentEventKind.TOOL_PERMISSION
        )
        assert permission.data["effect"] == "allow"
        assert permission.data["id"] == "call_1"
        assert AgentEventKind.TOOL_COMPLETED in kinds
        assert AgentEventKind.TOOL_FAILED not in kinds

    async def test_permission_ask_path_resolves_through_broker(self):
        """An ASK decision routes through the approval broker and executes on allow."""
        responses = [
            make_tool_call_response("call_1", "echo", '{"text": "ask"}'),
            make_text_response("Approved"),
        ]
        broker = SessionApprovalBroker()
        broker.set_handler(_approve_once)
        permission_manager = PermissionManager()  # default -> ASK for unknown tools
        agent, _ = _build_instrumented_agent(
            responses,
            permission_manager=permission_manager,
            approval_broker=broker,
        )
        sink = RecordingSink()

        await _run_agent(agent, "Run echo", sink)

        kinds = _kinds(sink.events)
        assert AgentEventKind.TOOL_APPROVAL_REQUESTED in kinds
        assert AgentEventKind.TOOL_APPROVAL_RESOLVED in kinds

        requested = next(
            e for e in sink.events if e.kind == AgentEventKind.TOOL_APPROVAL_REQUESTED
        )
        assert requested.data["id"] == "call_1"
        assert requested.data["name"] == "echo"

        resolved = next(
            e for e in sink.events if e.kind == AgentEventKind.TOOL_APPROVAL_RESOLVED
        )
        assert resolved.data["effect"] == "allow"
        assert resolved.data["outcome"] == "allow_once"

        # The approved tool still executes through the registry.
        assert AgentEventKind.TOOL_COMPLETED in kinds

    async def test_permission_deny_emits_tool_failed(self):
        """A DENY decision blocks execution and emits TOOL_FAILED instead."""
        responses = [
            make_tool_call_response("call_1", "echo", '{"text": "no"}'),
            make_text_response("Blocked"),
        ]
        permission_manager = PermissionManager(deny_rules=["echo"])
        agent, _ = _build_instrumented_agent(
            responses, permission_manager=permission_manager
        )
        sink = RecordingSink()

        await _run_agent(agent, "Run echo", sink)

        kinds = _kinds(sink.events)
        assert AgentEventKind.TOOL_FAILED in kinds
        assert AgentEventKind.TOOL_STARTED not in kinds
        assert AgentEventKind.TOOL_COMPLETED not in kinds

        failed = next(
            e for e in sink.events if e.kind == AgentEventKind.TOOL_FAILED
        )
        assert "permission denied" in failed.data["output"]
        assert failed.data["is_error"] is True


# ─── TuiAgentRunner ────────────────────────────────────────────────────────────


class TestTuiAgentRunner:
    async def test_runner_assembles_text_response(self):
        """The runner drives a real Agent and returns an assembled AgentRunResult."""
        agent, _ = _build_instrumented_agent([make_text_response("Hello world!")])
        runner = TuiAgentRunner(agent, session_id="sess-1")
        sink = RecordingSink()

        result = await runner.run("Say hi", sink=sink)

        assert result.session_id == "sess-1"
        assert result.response == "Hello world!"
        assert result.steps >= 1

        # TURN_COMPLETED is the final terminal event pushed to the sink.
        assert _kinds(sink.events)[-1] == AgentEventKind.TURN_COMPLETED
        assert result.events[-1].kind == AgentEventKind.TURN_COMPLETED

        # An assistant message is present; the assembled text lives in `response`
        # because the Agent streams text_deltas without persisting them to the
        # session (the session's assistant message has empty content).
        assert Role.ASSISTANT in [m.role for m in result.messages]

        # The runner detaches the sink once the turn completes.
        assert agent._sink is None

    async def test_runner_handles_tool_turn(self):
        """A tool-call turn yields a TOOL domain message and the final text."""
        responses = [
            make_tool_call_response("call_1", "echo", '{"text": "tooled"}'),
            make_text_response("Final answer"),
        ]
        agent, _ = _build_instrumented_agent(responses)
        runner = TuiAgentRunner(agent, session_id="sess-2")

        result = await runner.run("Run the tool")

        assert result.response == "Final answer"
        assert result.events[-1].kind == AgentEventKind.TURN_COMPLETED

        tool_msg = next(m for m in result.messages if m.role == Role.TOOL)
        assert tool_msg.tool_call_id == "call_1"
        assert "Echo: tooled" in tool_msg.content

    async def test_runner_reports_actual_step_count(self):
        """Two model turns (tool call + text) should report 2 steps."""
        responses = [
            make_tool_call_response("call_1", "echo", '{"text": "x"}'),
            make_text_response("Final answer"),
        ]
        agent, _ = _build_instrumented_agent(responses)
        runner = TuiAgentRunner(agent, session_id="sess-steps")

        result = await runner.run("Run it")

        assert result.steps == 2


# ─── SessionApprovalBroker ─────────────────────────────────────────────────────


class TestSessionApprovalBroker:
    async def test_handler_allow_once(self):
        """An ALLOW_ONCE approval resolves to allowed."""
        broker = SessionApprovalBroker()
        broker.set_handler(_approve_once)
        request = build_permission_request("c1", "bash", {"command": "ls"}, "r")

        approval = await broker.request(request)

        assert approval.allowed
        assert approval.kind == PermissionApprovalKind.ALLOW_ONCE

    async def test_handler_deny(self):
        """A DENY approval resolves to not allowed."""
        broker = SessionApprovalBroker()

        async def _deny(request: PermissionRequest) -> PermissionApproval:
            return PermissionApproval.deny("user said no")

        broker.set_handler(_deny)
        request = build_permission_request("c1", "bash", {"command": "ls"}, "r")

        approval = await broker.request(request)

        assert not approval.allowed
        assert approval.kind == PermissionApprovalKind.DENY
        assert approval.reason == "user said no"

    async def test_allow_session_reused_for_identical_scope(self):
        """An allow_session approval is cached and reused for identical actions."""
        handler = _CountingHandler(PermissionApproval.allow_session("ok"))
        broker = SessionApprovalBroker()
        broker.set_handler(handler)

        request1 = build_permission_request("c1", "bash", {"command": "ls -la"}, "r")
        request2 = build_permission_request("c2", "bash", {"command": "ls -la"}, "r")

        approval1 = await broker.request(request1)
        assert approval1.kind == PermissionApprovalKind.ALLOW_SESSION
        assert handler.calls == 1

        # Identical action -> cached session-scope approval, handler not re-invoked.
        approval2 = await broker.request(request2)
        assert approval2.kind == PermissionApprovalKind.ALLOW_SESSION
        assert approval2.allowed
        assert "identical action" in approval2.reason
        assert handler.calls == 1

        # Different arguments -> a new scope, handler consulted again.
        request3 = build_permission_request("c3", "bash", {"command": "pwd"}, "r")
        approval3 = await broker.request(request3)
        assert approval3.allowed
        assert handler.calls == 2

    async def test_headless_no_handler_denies(self):
        """Without a handler the broker denies (headless UI)."""
        broker = SessionApprovalBroker()
        request = build_permission_request("c1", "bash", {"command": "ls"}, "r")

        approval = await broker.request(request)

        assert not approval.allowed
        assert approval.kind == PermissionApprovalKind.DENY
        assert "interactive approval UI is unavailable" in approval.reason

    async def test_allow_session_without_scope_downgraded_to_once(self):
        """ALLOW_SESSION without a scoped key degrades to ALLOW_ONCE."""
        handler = _CountingHandler(PermissionApproval.allow_session("ok"))
        broker = SessionApprovalBroker()
        broker.set_handler(handler)
        request = PermissionRequest(
            call_id="c1", tool_name="bash", summary="s", reason="r", scope_key=None
        )

        approval = await broker.request(request)

        assert approval.kind == PermissionApprovalKind.ALLOW_ONCE
        assert approval.allowed
        assert handler.calls == 1


# ─── build_permission_request ──────────────────────────────────────────────────


class TestBuildPermissionRequest:
    def test_basic_fields_and_scope_key(self):
        """Returns a PermissionRequest with correct fields and a hashed scope key."""
        request = build_permission_request(
            "call-1", "echo", {"text": "hi"}, "requires approval"
        )

        assert request.call_id == "call-1"
        assert request.tool_name == "echo"
        assert request.reason == "requires approval"
        assert request.scope_key is not None
        assert len(request.scope_key) == 64  # sha256 hex digest
        assert "echo" in request.summary

    def test_scope_key_stable_for_identical_arguments(self):
        """Identical arguments hash to the same scope key regardless of ordering."""
        request1 = build_permission_request("a", "echo", {"text": "hi", "num": 1}, "r")
        request2 = build_permission_request("b", "echo", {"num": 1, "text": "hi"}, "r")
        request3 = build_permission_request("c", "echo", {"text": "bye", "num": 1}, "r")

        assert request1.scope_key == request2.scope_key
        assert request1.scope_key != request3.scope_key

    def test_bash_command_summary_truncated(self):
        """Long bash commands are bounded in the summary with a truncation marker."""
        long_command = "x" * 3000
        request = build_permission_request("c1", "bash", {"command": long_command}, "r")

        assert request.summary.startswith("Run shell command:")
        assert "… [truncated]" in request.summary
        assert len(request.summary) < len(long_command)

    def test_file_edit_summary(self):
        """file_edit summaries name the target path and replace scope."""
        request = build_permission_request(
            "c1", "file_edit", {"file_path": "src/app.py", "replace_all": True}, "r"
        )
        assert "Edit workspace file: src/app.py" in request.summary
        assert "all matching occurrences" in request.summary

        single = build_permission_request(
            "c2", "file_edit", {"file_path": "src/app.py"}, "r"
        )
        assert "one occurrence" in single.summary


# ─── to_domain_items ───────────────────────────────────────────────────────────


class TestToDomainItems:
    def test_converts_session_messages(self):
        """Each code_agent message type maps to the matching TUI domain message."""
        session = Session(system_prompt="System prompt")
        session.add_user_message("User question")
        session.add_assistant_message(
            tool_calls=[ToolCallItem(id="t1", name="echo", arguments='{"text": "hi"}')]
        )
        session.add_tool_result("t1", "Echo: hi")
        session.add_assistant_message(text="Final answer")

        items = to_domain_items(session.messages)

        assert [item.role for item in items] == [
            Role.SYSTEM,
            Role.USER,
            Role.ASSISTANT,
            Role.TOOL,
            Role.ASSISTANT,
        ]
        assert items[0].content == "System prompt"
        assert items[1].content == "User question"

        assistant_call = items[2]
        assert len(assistant_call.tool_calls) == 1
        call = assistant_call.tool_calls[0]
        assert call.id == "t1"
        assert call.name == "echo"
        assert dict(call.arguments) == {"text": "hi"}

        tool_msg = items[3]
        assert tool_msg.tool_call_id == "t1"
        assert tool_msg.content == "Echo: hi"

        assert items[4].content == "Final answer"

    def test_invalid_tool_call_json_parses_to_empty_dict(self):
        """Malformed tool-call arguments degrade to an empty mapping."""
        session = Session(system_prompt="System prompt")
        session.add_assistant_message(
            tool_calls=[ToolCallItem(id="t2", name="echo", arguments="not json{{")]
        )

        items = to_domain_items(session.messages)

        assistant = next(item for item in items if item.tool_calls)
        assert dict(assistant.tool_calls[0].arguments) == {}


# ─── InteractionModeController ─────────────────────────────────────────────────


class TestInteractionModeController:
    def _make(self, initial=InteractionMode.NORMAL):
        from code_agent.core.plan_mode import PlanModeTracker
        from pathlib import Path

        tracker = PlanModeTracker(Path.cwd() / "plan.md")
        pm = PermissionManager()
        ctrl = InteractionModeController(
            plan_tracker=tracker,
            permission_manager=pm,
            ui_preferences=None,
            initial=initial,
        )
        return ctrl, tracker, pm

    async def test_switch_to_plan_activates_tracker(self):
        ctrl, tracker, _ = self._make()
        result = await ctrl.set_interaction_mode(InteractionMode.PLAN)
        assert result.changed is True
        # activate() → PENDING; the tracker becomes ACTIVE on the next prompt
        # (PlanModeHook.on_turn_start calls on_prompt_arrived).
        assert tracker.state.value == "pending"

    async def test_noop_selection_still_applies_backends(self):
        """Startup misalignment: mode restored as PLAN but tracker inactive.

        A no-op selection (same mode) must still apply the backend state,
        otherwise the UI shows plan while writes are actually allowed.
        """
        ctrl, tracker, _ = self._make(initial=InteractionMode.PLAN)
        assert tracker.state.value == "inactive"  # restored, not applied
        result = await ctrl.set_interaction_mode(InteractionMode.PLAN)
        assert result.changed is False
        assert tracker.state.value == "pending"  # now applied

    async def test_auto_sets_yolo_even_on_noop(self):
        ctrl, tracker, pm = self._make(initial=InteractionMode.AUTO)
        assert pm.yolo_mode is False  # restored, not applied
        await ctrl.set_interaction_mode(InteractionMode.AUTO)
        assert pm.yolo_mode is True

    async def test_leaving_plan_deactivates_tracker(self):
        ctrl, tracker, _ = self._make()
        await ctrl.set_interaction_mode(InteractionMode.PLAN)
        await ctrl.set_interaction_mode(InteractionMode.NORMAL)
        assert tracker.is_active() is False
