"""Sub-agents must obey parent policy and reject invalid capabilities."""

import json
from argparse import Namespace
from unittest.mock import AsyncMock

import pytest

from code_agent.api.schemas import ProviderConfig
from code_agent.cli.assembly import build_subagent_factory
from code_agent.core.plan_mode import PlanModeTracker
from code_agent.permission.manager import PermissionManager
from code_agent.subagents.capabilities import capability_tool_names
from code_agent.tools.builtin.task import TaskInput
from conftest import MockChatClient, make_text_response, make_tool_call_response


@pytest.mark.parametrize("policy", ["default", "deny", "plan", "allow"])
async def test_child_file_writes_follow_parent_policy(policy, monkeypatch, tmp_path):
    target = tmp_path / "child.txt"
    client = MockChatClient()
    client.close = AsyncMock()
    client.set_responses([
        make_tool_call_response("c1", "file_write", json.dumps({"file_path": str(target), "content": "child"})),
        make_text_response("done"),
    ])
    monkeypatch.setattr("code_agent.subagents.factory.build_resilient_client", lambda *a: client)
    pm = PermissionManager(
        yolo_mode=policy in {"deny", "plan", "allow"},
        deny_rules=["Edit"] if policy == "deny" else [],
    )
    tracker = PlanModeTracker(tmp_path / "plan.md")
    if policy == "plan":
        tracker.activate()
    spawn = build_subagent_factory(
        Namespace(max_turns=3), ProviderConfig(),
        permission_manager=pm, plan_tracker=tracker,
    )

    assert await spawn("write the file") == "done"
    assert target.exists() is (policy == "allow")
    if policy != "allow":
        assert any("Permission denied" in (m.get("content") or "") for m in client.call_history[1]["messages"])
    client.close.assert_awaited_once()


def test_invalid_capability_does_not_grant_all_tools():
    with pytest.raises(ValueError):
        capability_tool_names("read-onyl")
    with pytest.raises(ValueError):
        TaskInput(prompt="inspect", capability_mode="all")


async def test_plan_mode_blocks_child_deletion_even_with_yolo(monkeypatch, tmp_path):
    target = tmp_path / "keep.txt"
    target.write_text("keep", encoding="utf-8")
    client = MockChatClient()
    client.set_responses([
        make_tool_call_response("c1", "file_delete", json.dumps({"file_path": str(target)})),
        make_text_response("done"),
    ])
    monkeypatch.setattr("code_agent.subagents.factory.build_resilient_client", lambda *a: client)
    tracker = PlanModeTracker(tmp_path / "plan.md")
    tracker.activate()
    spawn = build_subagent_factory(
        Namespace(max_turns=3), ProviderConfig(),
        permission_manager=PermissionManager(yolo_mode=True), plan_tracker=tracker,
    )
    await spawn("delete the file")
    assert target.read_text(encoding="utf-8") == "keep"
    assert any("Permission denied" in (m.get("content") or "") for m in client.call_history[1]["messages"])
