# [Phase 5] 2026-08-04: Feature-effectiveness tests (real Agent loop)
"""Feature-effectiveness tests — prove each remediation feature works in the
REAL Agent loop, not just that a helper returns a value.

Design doc: design-docs/测试方案-功能有效性.md
Each test drives a real ``Agent`` (real ToolRegistry + real hooks) with a
``MockChatClient`` standing in for the LLM, and asserts an OBSERVABLE outcome
(file not created, result text, timing, session contents, JSON output).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json

import pytest

from code_agent.api.schemas import StreamEvent, ToolCallDelta
from code_agent.core.agent import Agent
from code_agent.core.session import Session
from code_agent.cli.assembly import build_tools
from code_agent.tools.result import ToolResult

from conftest import MockChatClient, make_text_response


def _tool_turn(call_id: str, name: str, args_json: str) -> list[StreamEvent]:
    """A model turn that emits one complete tool call."""
    return [
        StreamEvent(kind="tool_call_delta",
                    tool_call=ToolCallDelta(index=0, id=call_id, name=name)),
        StreamEvent(kind="tool_call_delta",
                    tool_call=ToolCallDelta(index=0, arguments_fragment=args_json)),
        StreamEvent(kind="turn_end"),
    ]


async def _run(agent: Agent, prompt: str) -> None:
    async for _ in agent.run(prompt):
        pass


def _tool_msgs(agent: Agent):
    return [m for m in agent._session.messages if m.role == "tool"]


# ─── P0.1: permission fail-closed in the agent loop ────────────────────────────


class TestPermissionFailClosed:
    async def test_write_tool_blocked_in_headless(self, tmp_path):
        """A headless-style Agent DENIES a write tool: file untouched + error."""
        from code_agent.hooks.phase4_hooks import PermissionHook
        from code_agent.permission.manager import PermissionManager

        target = tmp_path / "should_not_exist.txt"
        registry = build_tools()
        client = MockChatClient()
        client.set_responses([
            _tool_turn("c1", "file_write",
                       json.dumps({"file_path": str(target), "content": "boom"})),
            make_text_response("done"),
        ])
        agent = Agent(
            Session(system_prompt="T"), client, registry,
            max_turns=5,
            hooks=[PermissionHook(PermissionManager(), ask_action="deny")],
        )
        await _run(agent, "write a file")

        assert not target.exists()  # the write never happened
        msgs = _tool_msgs(agent)
        assert len(msgs) == 1
        assert msgs[0].is_error
        assert "denied" in msgs[0].content.lower()


# ─── P5.1: parallel tool execution is truly concurrent ─────────────────────────


class TestParallelTools:
    async def test_two_slow_tools_run_concurrently(self):
        """Two 0.3s tools in one turn finish in ~0.3s, not ~0.6s (serial)."""
        from pydantic import BaseModel, Field

        from code_agent.tools.registry import ToolRegistry

        registry = ToolRegistry()

        class SlowInput(BaseModel):
            tag: str = Field(default="x")

        async def slow(input: SlowInput) -> ToolResult:
            await asyncio.sleep(0.3)
            return ToolResult.ok(f"done-{input.tag}")

        registry.register("slow", SlowInput, description="sleepy")(slow)
        registry.finalize()

        client = MockChatClient()
        # Two tool calls in the SAME turn (indices 0 and 1) → parallel path.
        client.set_responses([[
            StreamEvent(kind="tool_call_delta",
                        tool_call=ToolCallDelta(index=0, id="c1", name="slow")),
            StreamEvent(kind="tool_call_delta",
                        tool_call=ToolCallDelta(index=0, arguments_fragment='{"tag":"a"}')),
            StreamEvent(kind="tool_call_delta",
                        tool_call=ToolCallDelta(index=1, id="c2", name="slow")),
            StreamEvent(kind="tool_call_delta",
                        tool_call=ToolCallDelta(index=1, arguments_fragment='{"tag":"b"}')),
            StreamEvent(kind="turn_end"),
        ], make_text_response("done")])
        agent = Agent(Session(system_prompt="T"), client, registry, max_turns=5)

        start = asyncio.get_event_loop().time()
        await _run(agent, "run two slow tools")
        elapsed = asyncio.get_event_loop().time() - start

        # Serial would be ~0.6s; parallel is ~0.3s. Assert clearly parallel.
        assert elapsed < 0.5, f"tools ran serially: {elapsed:.2f}s"
        results = [m.content for m in _tool_msgs(agent)]
        assert results == ["done-a", "done-b"]


# ─── P5.2: todo_write + TodoHook work together in the loop ─────────────────────


class TestTodoEndToEnd:
    async def test_todo_write_then_reminder_injected(self):
        from code_agent.todo.controller import TodoController
        from code_agent.todo.hook import TodoHook

        controller = TodoController()
        registry = build_tools(todo_controller=controller)
        client = MockChatClient()
        client.set_responses([
            _tool_turn("c1", "todo_write",
                       json.dumps({"todos": [
                           {"id": "t1", "content": "Step one",
                            "status": "in_progress"},
                       ]})),
            make_text_response("done"),
        ])
        agent = Agent(
            Session(system_prompt="T"), client, registry,
            max_turns=5, hooks=[TodoHook(controller)],
        )
        await _run(agent, "track my steps")

        # The todo landed in the controller.
        assert controller.snapshot()[0].content == "Step one"
        # And the NEXT turn's start injected it as a reminder into the session.
        texts = [m.content for m in agent._session.messages if m.role == "user"]
        assert any("Step one" in t for t in texts)


# ─── P7: task tool spawns a sub-agent and returns its result to the parent ─────


class TestSubagentEndToEnd:
    async def test_task_tool_result_lands_in_parent_session(self):
        async def fake_spawn(prompt, subagent_type, capability_mode):
            assert prompt == "research the bug"
            return "found the bug in src/main.py"

        registry = build_tools(subagent_factory=fake_spawn)
        client = MockChatClient()
        client.set_responses([
            _tool_turn("c1", "task", '{"prompt":"research the bug"}'),
            make_text_response("done"),
        ])
        agent = Agent(Session(system_prompt="T"), client, registry, max_turns=5)
        await _run(agent, "use a subagent")

        msgs = _tool_msgs(agent)
        assert len(msgs) == 1
        assert "<subagent_result" in msgs[0].content
        assert "found the bug in src/main.py" in msgs[0].content


# ─── P6: doom loop is broken inside the agent loop ─────────────────────────────


class TestDoomLoopEndToEnd:
    async def test_fourth_identical_call_is_denied(self):
        from code_agent.hooks.doom_loop_hook import DoomLoopHook

        registry = build_tools()
        client = MockChatClient()
        client.set_responses([
            _tool_turn("c1", "shell_exec", '{"command":"echo hi"}'),
            _tool_turn("c2", "shell_exec", '{"command":"echo hi"}'),
            _tool_turn("c3", "shell_exec", '{"command":"echo hi"}'),
            _tool_turn("c4", "shell_exec", '{"command":"echo hi"}'),
            make_text_response("done"),
        ])
        agent = Agent(
            Session(system_prompt="T"), client, registry,
            max_turns=10, hooks=[DoomLoopHook(repeat_limit=3)],
        )
        await _run(agent, "keep running echo")

        msgs = _tool_msgs(agent)
        assert len(msgs) == 4
        ok = [m for m in msgs if not m.is_error]
        denied = [m for m in msgs if m.is_error]
        assert len(ok) == 3
        assert len(denied) == 1
        assert "denied" in denied[0].content.lower()


# ─── P2.1: real usage flows into headless JSON ─────────────────────────────────


class TestHeadlessJsonUsage:
    async def test_usage_not_zero(self, capsys):
        from code_agent.cli.headless import run_headless_json

        client = MockChatClient()
        client.set_responses([[
            StreamEvent(kind="text_delta", text="Answer"),
            StreamEvent(kind="turn_end", usage={
                "prompt_tokens": 120, "completion_tokens": 30,
            }),
        ]])
        agent = Agent(Session(system_prompt="T"), client, build_tools())
        await run_headless_json(agent, "hi")

        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["usage"]["input_tokens"] == 120
        assert data["usage"]["output_tokens"] == 30
        assert data["usage"]["total_tokens"] == 150


# ─── P0.2: --sandbox actually delegates to DockerSandbox ───────────────────────


class TestSandboxDelegation:
    async def test_shell_exec_uses_sandbox_path(self, monkeypatch):
        from code_agent.sandbox.manager import SandboxManager

        async def fake_is_available(self):
            return True

        async def fake_run_sandbox(self, command, cwd, timeout, decision):
            return ToolResult.error("sandboxed")

        monkeypatch.setattr(SandboxManager, "is_available", fake_is_available)
        monkeypatch.setattr(SandboxManager, "_run_sandbox", fake_run_sandbox)
        registry = build_tools(sandbox=True)
        result = await registry.execute(
            "shell_exec", {"command": "rm -rf /tmp/sandbox-probe"}
        )

        assert result.is_error
        assert result.output == "sandboxed"


# ─── P0.2: skills list is injected into the session ────────────────────────────


class TestSkillsInjection:
    async def test_available_skills_injected(self, tmp_path):
        from code_agent.cli.assembly import inject_available_skills
        from code_agent.skills.manager import SkillManager

        skill_dir = tmp_path / ".code-agent" / "skills" / "demo"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: demo skill\n---\nDo the thing.\n",
            encoding="utf-8",
        )

        mgr = SkillManager(cwd=tmp_path)
        await mgr.load_all()
        session = Session(system_prompt="T")

        assert inject_available_skills(session, mgr) is True
        texts = [m.content for m in session.messages if m.role == "user"]
        assert any("demo-skill" in t for t in texts)

    async def test_plan_flag_activates_tracker(self):
        import argparse
        from pathlib import Path

        from code_agent.cli.assembly import activate_plan_mode
        from code_agent.core.plan_mode import PlanModeTracker

        tracker = PlanModeTracker(Path("plan.md"))
        activate_plan_mode(argparse.Namespace(plan=True), {"plan_tracker": tracker})
        assert tracker.state.value == "pending"

        # Without the flag, nothing happens.
        tracker2 = PlanModeTracker(Path("plan.md"))
        activate_plan_mode(argparse.Namespace(plan=False), {"plan_tracker": tracker2})
        assert tracker2.state.value == "inactive"


# ─── 2026-08-05: memory write path + headless pre-approvals ───────────────────


class TestMemoryWriteEndToEnd:
    async def test_knowledge_persists_across_two_agents(self, tmp_path):
        """Session 1's conversation is indexed; Session 2 retrieves it."""
        from code_agent.hooks.memory_write_hook import MemoryWriteHook
        from code_agent.hooks.phase2_hooks import MemoryHook
        from code_agent.memory.file_store import MemoryStore
        from code_agent.memory.index_db import MemoryIndex

        index = MemoryIndex(tmp_path / "memory" / "index.db")
        await index.open()
        try:
            store = MemoryStore(root_dir=tmp_path / "memory")
            cwd = str(tmp_path / "project")

            # Session 1: the agent states a fact; the write hook persists it.
            s1 = Session(system_prompt="T")
            s1._session_id = "sess-1"
            client1 = MockChatClient()
            client1.set_responses([make_text_response("done")])
            agent1 = Agent(
                s1, client1, build_tools(), max_turns=3,
                hooks=[MemoryWriteHook(index, store, cwd)],
            )
            await _run(agent1, "Remember: the pytest command is pytest -q")

            # Session 2: MemoryHook searches and injects the memory context.
            s2 = Session(system_prompt="T")
            client2 = MockChatClient()
            client2.set_responses([make_text_response("ok")])
            agent2 = Agent(
                s2, client2, build_tools(), max_turns=3,
                hooks=[MemoryHook(index)],
            )
            await _run(agent2, "What is the pytest command?")

            texts = [m.content for m in s2.messages if m.role == "user"]
            assert any("pytest -q" in t for t in texts)
        finally:
            await index.close()


class TestHeadlessPreApprovals:
    async def test_file_glob_allowed_in_headless(self, tmp_path):
        from code_agent.hooks.phase4_hooks import PermissionHook
        from code_agent.permission.manager import PermissionManager

        registry = build_tools()
        client = MockChatClient()
        client.set_responses([
            _tool_turn(
                "c1", "file_glob",
                json.dumps({"pattern": "*.py", "path": str(tmp_path)}),
            ),
            make_text_response("done"),
        ])
        agent = Agent(
            Session(system_prompt="T"), client, registry, max_turns=5,
            hooks=[PermissionHook(PermissionManager(), ask_action="deny")],
        )
        await _run(agent, "list files")

        msgs = _tool_msgs(agent)
        assert len(msgs) == 1
        assert not msgs[0].is_error

    async def test_exit_plan_mode_allowed_in_headless(self, tmp_path):
        from code_agent.core.plan_mode import PlanModeTracker
        from code_agent.hooks.phase4_hooks import PermissionHook
        from code_agent.permission.manager import PermissionManager

        tracker = PlanModeTracker(tmp_path / "plan.md")
        registry = build_tools(plan_tracker=tracker)
        client = MockChatClient()
        client.set_responses([
            _tool_turn("c1", "exit_plan_mode", '{"plan":"implement x"}'),
            make_text_response("done"),
        ])
        agent = Agent(
            Session(system_prompt="T"), client, registry, max_turns=5,
            hooks=[PermissionHook(PermissionManager(), ask_action="deny")],
        )
        await _run(agent, "submit the plan")

        msgs = _tool_msgs(agent)
        assert len(msgs) == 1
        assert not msgs[0].is_error
        assert "Plan Mode deactivated" in msgs[0].content

    async def test_task_still_gated_in_headless(self):
        from code_agent.hooks.phase4_hooks import PermissionHook
        from code_agent.permission.manager import PermissionManager

        async def fake_spawn(prompt, subagent_type, capability_mode):
            raise AssertionError("sub-agent must not spawn when permission denied")

        registry = build_tools(subagent_factory=fake_spawn)
        client = MockChatClient()
        client.set_responses([
            _tool_turn("c1", "task", '{"prompt":"spawn me"}'),
            make_text_response("done"),
        ])
        agent = Agent(
            Session(system_prompt="T"), client, registry, max_turns=5,
            hooks=[PermissionHook(PermissionManager(), ask_action="deny")],
        )
        await _run(agent, "use a subagent")

        msgs = _tool_msgs(agent)
        assert len(msgs) == 1
        assert msgs[0].is_error
        assert "denied" in msgs[0].content.lower()


# ─── 2026-08-05: compaction → persistence consistency in the real loop ────────


class TestCompactionPersistenceEndToEnd:
    async def test_compaction_rewrites_jsonl_in_agent_loop(self, tmp_path, monkeypatch):
        """[B-L3] Real Agent: compaction replaces history, JSONL is rewritten
        (old pre-compaction content must NOT remain in the file)."""
        from code_agent.compaction.policy import CompactionPolicy
        from code_agent.hooks.phase2_hooks import CompactionHook, PersistenceHook
        from code_agent.storage.session_store import SessionStore

        async def _small_summary(*a, **k):
            return "short summary"

        monkeypatch.setattr(
            "code_agent.hooks.phase2_hooks.generate_summary", _small_summary,
        )

        policy = CompactionPolicy(
            context_window=1000, threshold_percent=10, min_tokens=1,
        )
        store = SessionStore(root_dir=tmp_path / "sessions")
        cwd = str(tmp_path)

        session = Session(system_prompt="S")
        session.add_user_message("x" * 4000 + "OLD_MARKER")
        session.add_assistant_message(text="y" * 4000)

        client = MockChatClient()
        client.set_responses([make_text_response("done")])
        agent = Agent(
            session, client, build_tools(), max_turns=3,
            hooks=[CompactionHook(policy, client), PersistenceHook(store, cwd)],
        )
        await _run(agent, "small prompt")

        assert session._history_replaced is False
        _, msgs = await store.load_session(session._session_id, cwd)
        file_text = "\n".join(str(m) for m in msgs)
        assert "<conversation-history-summary>" in file_text
        assert "OLD_MARKER" not in file_text  # old history really replaced
        assert len(msgs) == len(session.to_api_messages())

    async def test_compaction_then_tool_turn_persists_in_order(self, tmp_path, monkeypatch):
        """[B-L3] After compaction the agent keeps working; tool turns persist
        with a valid message order and no orphan tool results."""
        from pydantic import BaseModel, Field

        from code_agent.compaction.policy import CompactionPolicy
        from code_agent.hooks.phase2_hooks import CompactionHook, PersistenceHook
        from code_agent.storage.session_store import SessionStore
        from code_agent.tools.registry import ToolRegistry

        async def _small_summary(*a, **k):
            return "short summary"

        monkeypatch.setattr(
            "code_agent.hooks.phase2_hooks.generate_summary", _small_summary,
        )

        registry = ToolRegistry()

        class EchoInput(BaseModel):
            text: str = Field(default="")

        @registry.register("echo", EchoInput, description="echo")
        async def echo(input: EchoInput) -> ToolResult:
            return ToolResult.ok(f"echo:{input.text}")

        registry.finalize()

        policy = CompactionPolicy(
            context_window=1000, threshold_percent=10, min_tokens=1,
        )
        store = SessionStore(root_dir=tmp_path / "sessions")
        cwd = str(tmp_path)

        session = Session(system_prompt="S")
        session.add_user_message("x" * 4000)
        session.add_assistant_message(text="y" * 4000)

        client = MockChatClient()
        client.set_responses([
            make_text_response("first"),
            _tool_turn("c1", "echo", '{"text":"hi"}'),
            make_text_response("second"),
        ])
        agent = Agent(
            session, client, registry, max_turns=6,
            hooks=[CompactionHook(policy, client), PersistenceHook(store, cwd)],
        )

        await _run(agent, "prompt one")
        await _run(agent, "prompt two")

        _, msgs = await store.load_session(session._session_id, cwd)
        api_msgs = session.to_api_messages()
        assert [m["role"] for m in msgs] == [m["role"] for m in api_msgs]
        assert len(msgs) == len(api_msgs)
        tool_msgs = [m for m in msgs if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"]  # paired, not orphan

    async def test_edited_files_in_reminder_in_agent_loop(self, tmp_path, monkeypatch):
        """[D-L3] Real Agent: edit a file, then a later turn compacts — the
        post-compaction reminder contains the edited path."""
        from code_agent.compaction.policy import CompactionPolicy
        from code_agent.hooks.phase2_hooks import CompactionHook, EditTrackerHook

        async def _small_summary(*a, **k):
            return "short summary"

        monkeypatch.setattr(
            "code_agent.hooks.phase2_hooks.generate_summary", _small_summary,
        )

        target = tmp_path / "a.py"
        big = tmp_path / "big.txt"
        big.write_text("z" * 3000, encoding="utf-8")

        policy = CompactionPolicy(
            context_window=1000, threshold_percent=10, min_tokens=1,
        )
        client = MockChatClient()
        client.set_responses([
            _tool_turn("c1", "file_write", json.dumps({
                "file_path": str(target), "content": "hello",
            })),
            _tool_turn("c2", "file_read", json.dumps({
                "file_path": str(big),
            })),
            make_text_response("done"),
        ])
        tracker = EditTrackerHook()
        agent = Agent(
            Session(system_prompt="T"), client, build_tools(), max_turns=5,
            hooks=[tracker, CompactionHook(policy, client, edit_tracker=tracker)],
        )
        await _run(agent, "edit then read the big file")

        assert target.exists()  # the edit really happened before compaction
        texts = [m.content for m in agent._session.messages if m.role == "user"]
        assert any("Edited files" in t for t in texts)
        assert any("a.py" in t for t in texts)
