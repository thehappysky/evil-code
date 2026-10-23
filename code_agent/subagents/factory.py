# [INT-08] 2026-08-09: Sub-agent spawn orchestration.
# Moved from cli/assembly.py. The full spawn_subagent closure (Session
# construction, capability-filtered tool registry, Agent creation, event
# collection, client cleanup, SubagentCoordinator assembly) belongs with
# the subagents package. cli/assembly.py keeps a thin wrapper that injects
# build_tools_fn and oauth_refresh_cb (CLI-layer dependencies) so callers
# stay unchanged.
"""Sub-agent spawn factory.

Builds an async sub-agent spawner bound to a parent config + sandbox.
The spawner creates a fresh Session (its own system prompt + the task
prompt), a resilient client, a capability-filtered tool registry WITHOUT
the task tool (no recursion), a loop guard, and parent permission/plan hooks.
The client is closed when the sub-agent finishes.

Dependencies ``build_tools_fn`` and ``oauth_refresh_cb`` are injected by
the caller (cli/assembly.py) to keep this module free of CLI-layer
imports — no circular dependency on cli.assembly.
"""

from __future__ import annotations

import argparse
import logging

from code_agent.api.client_factory import build_resilient_client
from code_agent.api.schemas import ProviderConfig
from code_agent.core.agent import Agent
from code_agent.core.session import Session
from code_agent.runtime import open_runtime
from code_agent.hooks.doom_loop_hook import DoomLoopHook
from code_agent.hooks.phase3_hooks import PlanModeHook
from code_agent.hooks.phase4_hooks import PermissionHook
from code_agent.permission.manager import PermissionManager
from code_agent.subagents.capabilities import capability_tool_names
from code_agent.prompts import (
    GENERAL_SUBAGENT_PROMPT,
    SUBAGENT_PROMPTS,
)
from code_agent.subagents.coordinator import SubagentCoordinator

logger = logging.getLogger(__name__)


def build_subagent_factory(
    args: argparse.Namespace,
    config: ProviderConfig,
    sandbox_manager=None,
    *,
    build_tools_fn,
    oauth_refresh_cb,
    permission_manager=None,
    plan_tracker=None,
):
    """Build an async sub-agent spawner (depth-guarded, isolated context).

    Args:
        args: CLI args namespace (reads ``args.max_turns``).
        config: Parent provider config for the sub-agent's LLM client.
        sandbox_manager: Optional shared SandboxManager passed through to
            ``build_tools_fn`` for shell_exec routing.
        build_tools_fn: Callable returning a ``ToolRegistry`` (typically
            ``cli.assembly.build_tools``). Injected to avoid a circular
            import on cli.assembly.
        oauth_refresh_cb: Async OAuth refresh callback for 401 handling
            (typically ``cli.auth.oauth_refresh_cb``). Injected to keep
            credential logic out of the subagents package.
        permission_manager: Parent policy; operations needing a new approval
            are denied in the non-interactive child. If absent, build a
            fail-closed policy from args.
        plan_tracker: Optional parent plan-mode tracker to prevent delegated
            writes from bypassing active plan mode.

    Returns:
        An async callable ``(prompt, subagent_type, capability_mode) ->
        str`` — the sub-agent's final text. Wrapped by
        ``SubagentCoordinator`` for depth guarding.
    """
    max_turns = args.max_turns
    if permission_manager is None:
        permission_manager = PermissionManager(
            yolo_mode=getattr(args, "yolo", False),
            allow_rules=getattr(args, "allow_rules", None),
            deny_rules=getattr(args, "deny_rules", None),
            prompt_policy="deny",
            sandbox_mode=sandbox_manager.mode if sandbox_manager else "off",
            sandbox_available=sandbox_manager.is_available if sandbox_manager else None,
        )

    async def spawn_subagent(
        prompt: str,
        subagent_type: str = "general-purpose",
        capability_mode: str = "read-write",
    ) -> str:
        # Reject unknown capability modes before allocating a client.
        capability_tool_names(capability_mode)
        session = Session(
            system_prompt=SUBAGENT_PROMPTS.get(subagent_type, GENERAL_SUBAGENT_PROMPT),
        )
        hooks = [DoomLoopHook(), PermissionHook(permission_manager)]
        if plan_tracker is not None:
            hooks.append(PlanModeHook(plan_tracker))
        async with open_runtime(
            session,
            create_client=lambda: build_resilient_client(config, oauth_refresh_cb),
            create_tools=lambda: build_tools_fn(
                capability_mode=capability_mode,
                sandbox_manager=sandbox_manager,
                allow_sandbox_fallback=False,
            ),
            create_agent=lambda session, client, tools: Agent(
                session, client, tools, max_turns=max_turns, hooks=hooks,
            ),
        ) as runtime:
            parts: list[str] = []
            async for event in runtime.agent.run(prompt):
                if event.kind == "text_delta" and event.text:
                    parts.append(event.text)
                elif event.kind == "error" and event.text:
                    parts.append(f"\n[error] {event.text}")
            return "".join(parts).strip() or "(no response)"

    coordinator = SubagentCoordinator(spawn_subagent, max_depth=1)
    return coordinator.spawn


__all__ = ["build_subagent_factory"]
