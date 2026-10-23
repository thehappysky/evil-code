"""Dependency assembly for the CLI.

Extracted from ``cli/main.py`` (M6). These are the assembly / infrastructure
functions that wire tools, hooks, sub-agents, Phase 2 modules, and the Agent
itself. ``main.py`` keeps only the entry points (``main``, ``_async_main``,
``_run_tui``) and imports what it needs from here.

Logic reference: Rust version main.rs run_agent_command().

Assembly flow:
    resolve_provider_config(args) -> Session() -> ChatClient(config)
    -> build_tools() -> build_agent()

build_agent() is THE integration point for the open-closed principle:
Phase 1 returns empty hooks; Phase 2-4 only modify this function to add
Hook instances. Agent source code never changes.

Note: the module-level ``config`` / ``trace`` globals still live in
``main.py`` (set by ``main()``, read by ``_async_main`` / ``_run_tui``).
None of the functions in this module reference those globals — where a
function needs ``config`` or ``trace`` it receives them as an argument.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from code_agent.api.client import ChatClient
from code_agent.api.schemas import ApiBackend, ProviderConfig
from code_agent.api.client_factory import build_resilient_client
from code_agent.cli.auth import oauth_refresh_cb
from code_agent.compaction.policy import CompactionPolicy
from code_agent.core.agent import Agent
from code_agent.core.hooks import AgentHook
from code_agent.core.plan_mode import PlanModeTracker, make_exit_plan_mode_tool
from code_agent.core.session import Session
from code_agent.telemetry.tracer import TraceWriter
from code_agent.hooks.dispatcher import HookDispatcher, HookEvent, HookResult
from code_agent.hooks.doom_loop_hook import DoomLoopHook
from code_agent.hooks.memory_write_hook import MemoryWriteHook
from code_agent.hooks.phase2_hooks import (
    CompactionHook,
    EditTrackerHook,
    MemoryHook,
    PersistenceHook,
)
from code_agent.hooks.phase3_hooks import PlanModeHook
from code_agent.hooks.phase4_hooks import (
    LifecycleHook,
    PermissionHook,
    TelemetryHook,
)
from code_agent.hooks.project_rules_hook import ProjectRulesHook
from code_agent.memory.file_store import MemoryStore, _project_hash
from code_agent.memory.index_db import MemoryIndex
from code_agent.permission.manager import PermissionManager
from code_agent.plugins.loader import PluginLoader
from code_agent.plugins.models import PluginToolArgs
from code_agent.plugins.registry import hook_event_name_for, register_plugin_hook
from code_agent.prompts import (
    GENERAL_SUBAGENT_PROMPT,
    MAIN_SYSTEM_PROMPT,
    SUBAGENT_PROMPTS,
)
from code_agent.sandbox.manager import SandboxManager
from code_agent.skills.manager import (
    SkillManager,
    SkillTool,
    SkillToolInput,
)
from code_agent.storage.session_store import SessionStore
from code_agent.subagents.capabilities import capability_tool_names
from code_agent.subagents.coordinator import SubagentCoordinator
from code_agent.telemetry.logger import TelemetryLogger
from code_agent.todo.controller import TodoController
from code_agent.todo.hook import TodoHook
from code_agent.todo.tool import TodoWriteInput, TodoWriteTool
from code_agent.tools.builtin.file_delete import DeleteFileInput, file_delete
from code_agent.tools.builtin.file_edit import SearchReplaceInput, file_edit
from code_agent.tools.builtin.file_glob import GlobInput, file_glob
from code_agent.tools.builtin.file_read import ReadFileInput, file_read
from code_agent.tools.builtin.file_search import SearchInput, file_search
from code_agent.tools.builtin.file_write import WriteFileInput, file_write
from code_agent.tools.builtin.shell_exec import ShellInput, make_shell_exec_routed, shell_exec
from code_agent.tools.builtin.task import TaskInput, TaskTool
from code_agent.tools.builtin.web_fetch import WebFetchInput, web_fetch
from code_agent.tools.builtin.web_search import WebSearchInput, WebSearchTool
from code_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


# ─── Default System Prompt ──────────────────────────────────────────────────────

_MAIN_SYSTEM_PROMPT = MAIN_SYSTEM_PROMPT

# Sub-agent system prompts by role (from code_agent.prompts — grok-build adapted).
_GENERAL_SUBAGENT_PROMPT = GENERAL_SUBAGENT_PROMPT


# ─── Tool Registration ─────────────────────────────────────────────────────────


def build_tools(
    skill_manager=None,
    *,
    sandbox: bool = False,
    sandbox_manager=None,
    allow_sandbox_fallback: bool = True,
    plugin_tools: list | None = None,
    plan_tracker=None,
    web_search_config: "ProviderConfig | None" = None,
    todo_controller=None,
    subagent_factory=None,
    capability_mode: str | None = None,
    finalize: bool = True,
) -> ToolRegistry:
    """Register all built-in tools and (by default) finalize the registry.

    Phase 3: adds SkillTool when skill_manager is provided.
    Phase 4 wiring (2026-08-04):
        - sandbox: shell_exec delegates to DockerSandbox when True.
        - plugin_tools: [(plugin_name, callable), ...] registered as
          ``{plugin_name}__{fn_name}`` plugin tools (permissive input model).
        - plan_tracker: registers the ``exit_plan_mode`` tool so Plan Mode
          has a way out (previously a deadlock in headless).
        - web_search_config: when provided and the provider backend is
          RESPONSES, registers the ``web_search`` tool.
        - todo_controller: registers the ``todo_write`` tool.
        - subagent_factory: registers the ``task`` tool (spawn sub-agents).
        - capability_mode: when set, only the tools allowed for that mode
          survive finalize (used for sub-agent registries).
        - finalize=False lets the caller register MCP tools before locking.
    """
    import inspect

    registry = ToolRegistry()

    registry.register(
        "file_read", ReadFileInput,
        description="Read file contents with line numbers. Supports offset/limit for large files.",
        category="filesystem",
        safety="read",
    )(file_read)

    registry.register(
        "file_search", SearchInput,
        description="Search for regex patterns in files. Uses ripgrep if available, falls back to Python re.",
        category="filesystem",
        safety="read",
    )(file_search)

    # shell_exec — [Phase 7] 沙箱自动路由（safe → 本机；风险 → Docker 沙箱）
    # [INT-05] 2026-08-09: 路由闭包已下沉到 tools/builtin/shell_exec.py 的
    # make_shell_exec_routed 工厂,这里只负责装配。
    if sandbox_manager is None and sandbox:
        sandbox_manager = SandboxManager(mode="strict", cwd=os.getcwd())
    if sandbox_manager is not None:
        shell_exec_handler = make_shell_exec_routed(
            sandbox_manager, allow_fallback=allow_sandbox_fallback,
        )
        registry.register(
            "shell_exec", ShellInput,
            description=(
                "Execute a shell command. Safe commands run locally; "
                "risky commands run in a Docker sandbox."
            ),
            category="system",
            safety="execute",
        )(shell_exec_handler)
    else:
        registry.register(
            "shell_exec", ShellInput,
            description="Execute a shell command and return stdout/stderr. Supports timeout.",
            category="system",
            safety="execute",
        )(shell_exec)

    registry.register(
        "file_edit", SearchReplaceInput,
        description="Edit a file by replacing exact text matches. Requires unique match unless replace_all=true.",
        category="filesystem",
        safety="write",
    )(file_edit)

    registry.register(
        "file_write", WriteFileInput,
        description="Create a new file or overwrite an existing one. Auto-creates parent directories.",
        category="filesystem",
        safety="write",
    )(file_write)

    registry.register(
        "file_delete", DeleteFileInput,
        description="Delete a file or directory on the host. Requires approval (same path as file_write).",
        category="filesystem",
        safety="write",
    )(file_delete)

    registry.register(
        "web_fetch", WebFetchInput,
        description="Fetch a URL and return its content as Markdown. HTML is converted automatically.",
        category="web",
        safety="read",
    )(web_fetch)

    # [Phase 5 / Phase 4] 2026-08-04: glob — directory listing by pattern
    registry.register(
        "file_glob", GlobInput,
        description="List file paths matching a glob pattern (e.g. **/*.py). Respects .gitignore, includes hidden files. Results sorted by most recently modified, capped at 100.",
        category="filesystem",
        safety="read",
    )(file_glob)

    # [Phase 5 / Phase 4] 2026-08-04: web_search — only for Responses-capable providers
    if web_search_config is not None and web_search_config.api_backend == ApiBackend.RESPONSES:
        web_search_tool = WebSearchTool(web_search_config)
        registry.register(
            "web_search", WebSearchInput,
            description="Search the web and return a synthesized answer with sources. Use for questions requiring current information.",
            category="web",
            safety="read",
        )(web_search_tool.run)

    # [Phase 3] 2026-07-27: Skill tool — allows model to invoke user-defined skills
    if skill_manager is not None:
        skill_tool = SkillTool(manager=skill_manager)
        registry.register(
            "skill", SkillToolInput,
            description="Invoke a user-defined Skill by name. Use this to discover and execute project-specific workflows defined in SKILL.md files. Pass the skill name and optional arguments.",
            category="skill",
            safety="control",
        )(skill_tool.run)

    # [Phase 5] 2026-08-04: todo_write — structured task list
    if todo_controller is not None:
        todo_tool = TodoWriteTool(todo_controller)
        registry.register(
            "todo_write", TodoWriteInput,
            description=(
                "Create and manage a structured task list. The user sees this "
                "list live — it is your primary way to show progress. Use for "
                "any task with 3+ steps. Skip for trivial single-step work."
            ),
            category="plan",
            safety="control",
        )(todo_tool.run)

    # [Phase 4] 2026-08-04: plugin-contributed tools (were loaded but never registered)
    if plugin_tools:
        for plugin_name, fn in plugin_tools:
            # A plugin may attach a strict Pydantic input model as
            # __input_model__ on the callable; otherwise use permissive args.
            input_model = getattr(fn, "__input_model__", PluginToolArgs)

            async def _wrap_plugin_tool(input, fn=fn, model=input_model):
                kwargs = input.model_dump()
                result = fn(**kwargs)
                if inspect.isawaitable(result):
                    result = await result
                return str(result)

            registry.register(
                f"{plugin_name}__{fn.__name__}", input_model,
                description=f"Tool contributed by plugin '{plugin_name}'.",
                category="plugin",
            )(_wrap_plugin_tool)

    # [Phase 3] 2026-08-04: exit_plan_mode tool — gives Plan Mode a way out.
    # [INT-06] 2026-08-09: tool definition moved to core/plan_mode.py.
    if plan_tracker is not None:
        exit_input, exit_handler = make_exit_plan_mode_tool(plan_tracker)
        registry.register(
            "exit_plan_mode", exit_input,
            description="Submit the final implementation plan and exit Plan Mode. Call this when the plan is complete and ready for implementation.",
            category="plan",
            safety="control",
        )(exit_handler)

    # [Phase 7] 2026-08-04: task tool — spawn a sub-agent
    if subagent_factory is not None:
        task_tool = TaskTool(subagent_factory)
        registry.register(
            "task", TaskInput,
            description=(
                "Delegate a self-contained piece of work to a sub-agent that "
                "runs in an isolated context with a limited toolset, and "
                "returns its result. Use for research, focused exploration, "
                "or long subtasks that would otherwise bloat the main context."
            ),
            category="subagent",
            safety="execute",
        )(task_tool.run)

    # [Phase 7] 2026-08-04: capability filter — sub-agent tool registries only
    # keep the tools their capability_mode allows.
    if capability_mode is not None:
        allowed = capability_tool_names(capability_mode)
        if allowed is not None:
            registry._tools = {
                name: td for name, td in registry._tools.items() if name in allowed
            }

    if finalize:
        registry.finalize()
    return registry


# ─── Sub-agent helpers (Phase 7) ───────────────────────────────────────────────


# [INT-07] 2026-08-09: capability frozensets + capability_tool_names() moved
# to subagents/capabilities.py. The function is imported at the top of this
# module so build_tools() can call it; existing imports
# ``from code_agent.cli.assembly import capability_tool_names`` keep working.


def build_subagent_factory(
    args: argparse.Namespace,
    config: ProviderConfig,
    sandbox_manager=None,
    *,
    permission_manager=None,
    plan_tracker=None,
):
    """Thin wrapper that delegates to ``subagents.factory.build_subagent_factory``.

    [INT-08] 2026-08-09: The spawn orchestration (Session/Agent construction,
    event collection, client cleanup, SubagentCoordinator assembly) moved to
    ``code_agent/subagents/factory.py``. This wrapper injects the two
    CLI-layer dependencies (``build_tools`` and ``oauth_refresh_cb``) so
    callers stay unchanged and the subagents package has no circular import
    on cli.assembly.
    """
    from code_agent.subagents.factory import build_subagent_factory as _factory
    return _factory(
        args, config, sandbox_manager,
        build_tools_fn=build_tools,
        oauth_refresh_cb=oauth_refresh_cb,
        permission_manager=permission_manager,
        plan_tracker=plan_tracker,
    )


def inject_available_skills(session: "Session", skill_manager) -> bool:
    """Inject the available-skills list into the session (once, at startup).

    Extracted from ``_async_main`` so it is unit-testable.

    Args:
        session: The session to inject into.
        skill_manager: Loaded SkillManager.

    Returns:
        True if a skills list was injected.
    """
    try:
        skills_text = skill_manager.format_available_skills()
        if skills_text:
            session.add_user_message(skills_text)
            return True
    except Exception:
        logger.warning("Failed to inject skill list", exc_info=True)
    return False


def activate_plan_mode(args: argparse.Namespace, phase2: dict) -> None:
    """Activate Plan Mode from ``--plan`` for headless/REPL modes.

    Extracted from ``_async_main`` so it is unit-testable.
    """
    if getattr(args, "plan", False) and phase2.get("plan_tracker") is not None:
        phase2["plan_tracker"].activate()


# ─── Plugin Hook Registration (by name convention) ──────────────────────────────


# [INT-09] 2026-08-09: hook_event_name_for + register_plugin_hook moved to
# plugins/registry.py. Imported at the top of this module so init_phase2_modules
# can call register_plugin_hook; existing imports
# ``from code_agent.cli.assembly import hook_event_name_for`` keep working.


# ─── Phase 2 Infrastructure ────────────────────────────────────────────────────


def get_data_dir() -> Path:
    """Get the code-agent data directory (~/.code-agent)."""
    return Path.home() / ".code-agent"


def init_phase2_modules(args: argparse.Namespace):
    """Initialize Phase 2 infrastructure modules.

    Returns a dict of initialized modules, or empty dict if Phase 2
    features are disabled. Each module handles its own error resilience
    — initialization failure of one module doesn't affect others.

    Returns:
        Dict with keys: memory_index, session_store, compaction_policy,
        edit_tracker, memory_store.
    """
    modules: dict = {}
    data_dir = get_data_dir()

    # Memory index (cross-session memory search)
    if not getattr(args, "no_memory", False):
        try:
            cwd = os.path.abspath(getattr(args, "cwd", "."))
            project_hash = _project_hash(cwd)
            db_path = data_dir / "memory" / project_hash / "index.db"
            modules["memory_index"] = MemoryIndex(db_path)
        except Exception:
            logger.warning("Failed to init MemoryIndex", exc_info=True)

    # Session store (persistence)
    if not getattr(args, "no_persist", False):
        try:
            store = SessionStore(root_dir=data_dir / "sessions")
            modules["session_store"] = store
        except Exception:
            logger.warning("Failed to init SessionStore", exc_info=True)

    # Compaction policy
    if not getattr(args, "no_compaction", False):
        try:
            # Use 200k default; can be updated from model metadata later
            modules["compaction_policy"] = CompactionPolicy()
        except Exception:
            logger.warning("Failed to init CompactionPolicy", exc_info=True)

    # Edit tracker (always enabled — no I/O, no cost)
    modules["edit_tracker"] = EditTrackerHook()

    # [Phase 5] 2026-08-04: Todo controller — structured task list
    try:
        modules["todo_controller"] = TodoController()
    except Exception:
        logger.warning("Failed to init TodoController", exc_info=True)

    # Memory store (MEMORY.md I/O)
    try:
        modules["memory_store"] = MemoryStore()
    except Exception:
        logger.warning("Failed to init MemoryStore", exc_info=True)

    # [Phase 3] 2026-07-27: Plan Mode tracker — FSM for plan-first-then-code workflow
    try:
        cwd = os.path.abspath(getattr(args, "cwd", "."))
        plan_file = Path(cwd) / "plan.md"
        modules["plan_tracker"] = PlanModeTracker(plan_file_path=plan_file)
    except Exception:
        logger.warning("Failed to init PlanModeTracker", exc_info=True)

    # [Phase 3] 2026-07-27: Skill Manager — discover and load user-defined skills
    if not getattr(args, "no_skills", False):
        try:
            cwd = os.path.abspath(getattr(args, "cwd", "."))
            modules["skill_manager"] = SkillManager(cwd=Path(cwd))
        except Exception:
            logger.warning("Failed to init SkillManager", exc_info=True)

    # [Phase 4] 2026-07-29: Permission manager — 3-layer pipeline
    try:
        yolo = getattr(args, "yolo", False)
        allow_rules = getattr(args, "allow_rules", None) or []
        deny_rules = getattr(args, "deny_rules", None) or []
        modules["permission_mgr"] = PermissionManager(
            yolo_mode=yolo,
            allow_rules=allow_rules,
            deny_rules=deny_rules,
        )
    except Exception:
        logger.warning("Failed to init PermissionManager", exc_info=True)

    # [Phase 4] 2026-07-29: Lifecycle dispatcher — user hook scripts
    try:
        modules["lifecycle_dispatcher"] = HookDispatcher()
    except Exception:
        logger.warning("Failed to init HookDispatcher", exc_info=True)

    # [Phase 4] 2026-07-29: Plugin loader — discover and load plugins
    # [2026-08-04] Plugins now actually contribute tools (via build_tools)
    # and lifecycle hooks (via the dispatcher) instead of being discarded.
    if not getattr(args, "no_plugins", False):
        try:
            loader = PluginLoader()
            manifests = loader.discover()
            plugin_tools: list = []
            lifecycle = modules.get("lifecycle_dispatcher")
            for manifest in manifests:
                # Load plugin tools → registered into the registry in build_tools
                for fn in loader.load_tools(manifest):
                    plugin_tools.append((manifest.name, fn))
                # Load plugin hook handlers → register by name convention
                handlers = loader.load_hooks(manifest)
                if lifecycle and handlers:
                    for handler in handlers:
                        if not register_plugin_hook(lifecycle, handler):
                            logger.warning(
                                "Plugin '%s' hook '%s' has no matching "
                                "event name; skipped",
                                manifest.name,
                                getattr(handler, "__name__", "?"),
                            )
            modules["plugin_loader"] = loader
            if plugin_tools:
                modules["plugin_tools"] = plugin_tools
        except Exception:
            logger.warning("Failed to init PluginLoader", exc_info=True)

    # [Phase 4] 2026-08-04: Telemetry logger (was implemented but never wired)
    try:
        modules["telemetry_logger"] = TelemetryLogger()
    except Exception:
        logger.warning("Failed to init TelemetryLogger", exc_info=True)

    return modules


# ─── Session Resume ─────────────────────────────────────────────────────────────


async def resolve_session(
    args: argparse.Namespace,
) -> tuple[Session, dict]:
    """Resolve session: new, resume by ID, or resume latest.

    Returns:
        Tuple of (Session, phase2_modules dict).
    """
    phase2 = {}

    # Resume from --resume <id> or --continue
    if args.resume is not None or getattr(args, "continue_session", False):
        try:
            store = SessionStore(root_dir=get_data_dir() / "sessions")

            if args.resume is not None:
                session_id = args.resume
            else:
                # --continue: find latest for cwd
                latest = store.find_latest(os.path.abspath(args.cwd))
                if latest is None:
                    print(
                        f"No previous sessions found for {args.cwd}. "
                        "Starting new session.",
                        file=sys.stderr,
                    )
                    return Session(system_prompt=_MAIN_SYSTEM_PROMPT), phase2
                session_id = latest.session_id
                print(
                    f"Resuming latest session: {session_id} "
                    f"({latest.title or 'untitled'})",
                    file=sys.stderr,
                )

            meta, messages = await store.load_session(
                session_id, os.path.abspath(args.cwd),
            )

            if meta is None and not messages:
                print(
                    f"Session '{session_id}' not found. Starting new session.",
                    file=sys.stderr,
                )
                return Session(system_prompt=_MAIN_SYSTEM_PROMPT), phase2

            session = Session.from_dicts(messages)
            # Store session_id for persistence hook
            session._session_id = session_id
            print(
                f"Resumed session {session_id}: "
                f"{len(messages)} messages, "
                f"~{session.estimated_tokens} tokens",
                file=sys.stderr,
            )
            return session, phase2

        except Exception as exc:
            logger.warning("Session resume failed: %s", exc)
            print(
                f"Failed to resume session: {exc}. Starting new session.",
                file=sys.stderr,
            )
            return Session(system_prompt=_MAIN_SYSTEM_PROMPT), phase2

    return Session(system_prompt=_MAIN_SYSTEM_PROMPT), phase2


# ─── Agent Assembly (Open-Closed Integration Point) ────────────────────────────


def build_hooks(
    args: argparse.Namespace,
    session: Session,
    client: ChatClient,
    tools: ToolRegistry,
    *,
    phase2_modules: dict | None = None,
    include_permission_hook: bool = True,
) -> list[AgentHook]:
    """Build the AgentHook list (Phase 1 empty; Phase 2-4 append here).

    ``include_permission_hook`` is False in TUI mode: the TUI runner gates tool
    permissions itself (showing approval dialogs) instead of the headless
    PermissionHook's allow-by-default fallback.
    """
    hooks: list[AgentHook] = []
    phase2 = phase2_modules or {}

    # Phase 2: Memory hook (cross-session memory injection)
    memory_index = phase2.get("memory_index")
    if memory_index is not None:
        try:
            hooks.append(MemoryHook(memory_index))
        except Exception:
            logger.warning("Failed to create MemoryHook", exc_info=True)

    # Phase 2: Compaction hook (auto-compaction)
    compaction_policy = phase2.get("compaction_policy")
    if compaction_policy is not None:
        try:
            hooks.append(CompactionHook(
                compaction_policy, client,
                edit_tracker=phase2.get("edit_tracker"),
            ))
        except Exception:
            logger.warning("Failed to create CompactionHook", exc_info=True)

    # Phase 2: Persistence hook (session save)
    session_store = phase2.get("session_store")
    if session_store is not None:
        try:
            hooks.append(PersistenceHook(
                session_store, os.path.abspath(args.cwd),
            ))
        except Exception:
            logger.warning("Failed to create PersistenceHook", exc_info=True)

    # [2026-08-05] Memory write hook — closes the "memory is read-only" gap:
    # MemoryHook searched the index, but nothing ever wrote session content
    # into it. This hook persists session logs and reindexes MEMORY.md files.
    memory_store = phase2.get("memory_store")
    if memory_index is not None and memory_store is not None:
        try:
            hooks.append(MemoryWriteHook(
                memory_index, memory_store, os.path.abspath(args.cwd),
            ))
        except Exception:
            logger.warning("Failed to create MemoryWriteHook", exc_info=True)

    # Phase 2: Edit tracker (always enabled)
    edit_tracker = phase2.get("edit_tracker")
    if edit_tracker is not None:
        hooks.append(edit_tracker)

    # [Phase 6] 2026-08-04: Doom loop guard — breaks repeated identical tool calls
    try:
        hooks.append(DoomLoopHook())
    except Exception:
        logger.warning("Failed to create DoomLoopHook", exc_info=True)

    # [Phase 5] 2026-08-05: Project rules — inject AGENTS.md / CLAUDE.md once
    try:
        hooks.append(ProjectRulesHook(os.path.abspath(args.cwd)))
    except Exception:
        logger.warning("Failed to create ProjectRulesHook", exc_info=True)

    # [Phase 5] 2026-08-04: Todo hook — injects the task list each turn
    todo_controller = phase2.get("todo_controller")
    if todo_controller is not None:
        try:
            hooks.append(TodoHook(todo_controller))
        except Exception:
            logger.warning("Failed to create TodoHook", exc_info=True)

    # [Phase 3] 2026-07-27: Plan Mode hook — state machine controlling write access
    plan_tracker = phase2.get("plan_tracker")
    if plan_tracker is not None:
        try:
            hooks.append(PlanModeHook(plan_tracker))
        except Exception:
            logger.warning("Failed to create PlanModeHook", exc_info=True)

    # [Phase 4] 2026-07-29: Permission hook — 3-layer pipeline (YOLO → SafeCmd → UserConfirm)
    # [2026-08-04] Fail-closed: headless → ask_action="deny"; interactive REPL → "prompt".
    if include_permission_hook:
        permission_mgr = phase2.get("permission_mgr")
        if permission_mgr is not None:
            try:
                ask_action = (
                    "prompt"
                    if getattr(args, "prompt", None) is None
                    else "deny"
                )
                hooks.append(
                    PermissionHook(permission_mgr, ask_action=ask_action)
                )
            except Exception:
                logger.warning("Failed to create PermissionHook", exc_info=True)

    # [Phase 4] 2026-07-29: Lifecycle hook — user-defined hook scripts (Pre/PostToolUse)
    lifecycle_dispatcher = phase2.get("lifecycle_dispatcher")
    if lifecycle_dispatcher is not None:
        try:
            hooks.append(LifecycleHook(lifecycle_dispatcher))
        except Exception:
            logger.warning("Failed to create LifecycleHook", exc_info=True)

    # [Phase 4] 2026-08-04: Telemetry hook — logs session/turn lifecycle events
    telemetry_logger = phase2.get("telemetry_logger")
    if telemetry_logger is not None:
        try:
            hooks.append(TelemetryHook(telemetry_logger))
        except Exception:
            logger.warning("Failed to create TelemetryHook", exc_info=True)

    return hooks


def build_agent(
    args: argparse.Namespace,
    session: Session,
    client: ChatClient,
    tools: ToolRegistry,
    *,
    phase2_modules: dict | None = None,
    trace: TraceWriter | None = None,
) -> Agent:
    """Assemble Agent with hooks list.

    This function is the ONLY integration point — Agent source code
    never changes.
    """
    # [GRD-03] Backfill tool safety classification from registry to the
    # permission manager. The registry (tools) is finalized by this point,
    # so its safety metadata is authoritative. PermissionManager was
    # constructed earlier in init_phase2_modules with hardcoded fallbacks.
    if phase2_modules and "permission_mgr" in phase2_modules:
        pm = phase2_modules["permission_mgr"]
        pm.configure_tool_safety(
            read_tools=tools.names_by_safety("read"),
            control_tools=tools.names_by_safety("control"),
        )
    hooks = build_hooks(
        args, session, client, tools, phase2_modules=phase2_modules,
    )
    return Agent(
        session, client, tools,
        max_turns=args.max_turns,
        hooks=hooks,
        trace=trace,
    )


async def close_phase2(memory_index, trace: TraceWriter | None) -> None:
    """Close Phase 2 resources after any execution mode finishes."""
    if memory_index is not None:
        try:
            await memory_index.close()
        except Exception:
            pass
    if trace:
        trace.close()
        print(
            f"\n[Trace] {trace.session_id}.jsonl -> ~/.evil-code/traces/",
            file=sys.stderr,
        )


async def sandbox_gc_loop(mgr, interval: float = 600.0) -> None:
    """TUI 运行期定时 GC：默认每 10 分钟清理一次沙箱残留。"""
    while True:
        try:
            await asyncio.sleep(interval)
            stats = await mgr.gc()
            if stats.get("projects_evicted"):
                logger.info(
                    "Sandbox GC evicted %d project(s): %s",
                    stats["projects_evicted"], stats,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Sandbox GC failed", exc_info=True)
