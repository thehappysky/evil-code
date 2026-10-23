"""Integration tests for cli/main.py hook assembly.

The Phase 3/4 features (Plan Mode, Permissions, Lifecycle, Memory, Compaction,
Persistence) all reach the Agent through `_build_hooks`. These tests verify the
wiring actually happens — that real hook instances are assembled into the Agent
in headless mode, and that TUI mode excludes the headless PermissionHook.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from code_agent.cli.assembly import build_agent, build_hooks
from code_agent.compaction.policy import CompactionPolicy
from code_agent.core.plan_mode import PlanModeTracker
from code_agent.core.session import Session
from code_agent.hooks.dispatcher import HookDispatcher
from code_agent.hooks.phase2_hooks import CompactionHook, EditTrackerHook, MemoryHook, PersistenceHook
from code_agent.hooks.phase3_hooks import PlanModeHook
from code_agent.hooks.phase4_hooks import LifecycleHook, PermissionHook
from code_agent.hooks.memory_write_hook import MemoryWriteHook
from code_agent.memory.index_db import MemoryIndex
from code_agent.memory.file_store import MemoryStore
from code_agent.permission.manager import PermissionManager
from code_agent.storage.session_store import SessionStore
from code_agent.tools.registry import ToolRegistry
from code_agent.tools.result import ToolResult

from conftest import MockChatClient, make_text_response


def _make_phase2_modules(tmp_path: Path) -> dict:
    """Build a realistic phase2_modules dict with real instances."""
    return {
        "memory_index": MemoryIndex(tmp_path / "memory.db"),
        "memory_store": MemoryStore(tmp_path / "memory"),
        "compaction_policy": CompactionPolicy(context_window=200_000),
        "session_store": SessionStore(tmp_path / "sessions"),
        "edit_tracker": EditTrackerHook(),
        "plan_tracker": PlanModeTracker(tmp_path / "plan.md"),
        "permission_mgr": PermissionManager(yolo_mode=False),
        "lifecycle_dispatcher": HookDispatcher(),
    }


def _make_args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(cwd=str(tmp_path), max_turns=10)


def _make_client() -> MockChatClient:
    client = MockChatClient()
    client.set_responses([make_text_response("Hello from hooks")])
    return client


def _make_tools() -> ToolRegistry:
    registry = ToolRegistry()

    class EchoInput(BaseModel):
        text: str = Field(default="")

    @registry.register("echo", EchoInput, description="Echo tool")
    async def echo(input: EchoInput) -> ToolResult:
        return ToolResult.ok(f"Echo: {input.text}")

    registry.finalize()
    return registry


class TestBuildHooks:
    async def test_headless_assembles_all_hooks(self, tmp_path):
        """Headless mode wires every Phase 2-4 hook into the agent."""
        session = Session(system_prompt="Test")
        client = _make_client()
        tools = _make_tools()
        modules = _make_phase2_modules(tmp_path)

        hooks = build_hooks(
            _make_args(tmp_path), session, client, tools,
            phase2_modules=modules, include_permission_hook=True,
        )

        types = {type(h) for h in hooks}
        assert MemoryHook in types
        assert CompactionHook in types
        assert PersistenceHook in types
        assert MemoryWriteHook in types
        assert EditTrackerHook in types
        assert PlanModeHook in types
        assert PermissionHook in types
        assert LifecycleHook in types

    def test_tui_mode_excludes_permission_hook(self, tmp_path):
        """TUI gates permissions itself; the headless PermissionHook is excluded."""
        session = Session(system_prompt="Test")
        client = _make_client()
        tools = _make_tools()
        modules = _make_phase2_modules(tmp_path)

        hooks = build_hooks(
            _make_args(tmp_path), session, client, tools,
            phase2_modules=modules, include_permission_hook=False,
        )

        types = {type(h) for h in hooks}
        assert PermissionHook not in types
        # Everything else still wired.
        assert PlanModeHook in types
        assert MemoryHook in types
        assert MemoryWriteHook in types
        assert LifecycleHook in types

    def test_empty_modules_yields_only_always_on_guards(self, tmp_path):
        """A bare phase2_modules dict degrades gracefully to the always-on
        guards (DoomLoopHook + ProjectRulesHook) and nothing else."""
        from code_agent.hooks.doom_loop_hook import DoomLoopHook
        from code_agent.hooks.project_rules_hook import ProjectRulesHook

        session = Session(system_prompt="Test")
        client = _make_client()
        tools = _make_tools()

        hooks = build_hooks(
            _make_args(tmp_path), session, client, tools,
            phase2_modules={}, include_permission_hook=True,
        )
        types = {type(h) for h in hooks}
        assert types == {DoomLoopHook, ProjectRulesHook}


class TestBuildAgent:
    async def test_agent_runs_with_assembled_hooks(self, tmp_path):
        """_build_agent returns a working Agent whose hooks list is populated."""
        session = Session(system_prompt="Test")
        client = _make_client()
        tools = _make_tools()
        modules = _make_phase2_modules(tmp_path)

        agent = build_agent(
            _make_args(tmp_path), session, client, tools,
            phase2_modules=modules,
        )

        assert agent._hooks, "Agent should have hooks wired"
        assert type(agent._hooks[0]) in (
            MemoryHook, CompactionHook, PersistenceHook, EditTrackerHook,
            PlanModeHook, PermissionHook, LifecycleHook,
        )

        # The fully-wired agent still streams a text response end-to-end.
        events = []
        async for event in agent.run("Hi"):
            events.append(event)
        kinds = [e.kind for e in events]
        assert "text_delta" in kinds
        assert "turn_end" in kinds
        text = "".join(e.text or "" for e in events if e.kind == "text_delta")
        assert "Hello from hooks" in text
