"""CLI entry point - argparse + dependency assembly + headless/REPL modes.

Logic reference: Rust version main.rs run_agent_command().

The assembly / infrastructure functions (``build_tools``, ``build_hooks``,
``build_agent``, ``init_phase2_modules``, ``resolve_session``,
``close_phase2``, etc.) live in ``code_agent.cli.assembly``. This module
keeps only the entry points (``main``, ``_async_main``, ``_run_tui``) and
imports what it needs from there.

Assembly flow:
    resolve_provider_config(args) -> Session() -> ChatClient(config)
    -> build_tools() -> build_agent()

build_agent() is THE integration point for the open-closed principle:
Phase 1 returns empty hooks; Phase 2-4 only modify this function to add
Hook instances. Agent source code never changes.

Auth resolution (5-level priority chain):
    1. CLI args (--api-key, --base-url, --model)
    2. ~/.evil-code/config.yaml (evil-code native, auto-login)
    3. Environment variables (ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY)
    4. ~/.claude/settings.json env block (cc-switch managed)
    5. ~/.claude/.credentials.json OAuth token (claude login)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shlex
import sys
from pathlib import Path
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

from code_agent.cli.args import build_parser
from code_agent.api.client_factory import build_resilient_client
from code_agent.cli.auth import (
    config_from_profile,
    oauth_refresh_cb,
    resolve_provider_config,
)
from code_agent.telemetry.tracer import TraceWriter

if TYPE_CHECKING:
    from code_agent.api.client import ChatClient
    from code_agent.api.schemas import ApiBackend, ProviderConfig
    from code_agent.core.agent import Agent
    from code_agent.core.hooks import AgentHook
    from code_agent.core.exceptions import AgentError, AuthenticationError, MaxTurnsReached
    from code_agent.core.session import Session
    from code_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


# ─── Default System Prompt ──────────────────────────────────────────────────────

# [Phase 5] 2026-08-05: main system prompt centralized in code_agent/prompts.py
# (adapted from grok-build prompt.md, identity renamed to Evil).
# These re-exports stay here so external references (e.g. test_prompts wiring
# checks via code_agent.cli.main.SUBAGENT_PROMPTS) keep resolving; the
# sub-agent factory that consumes them lives in code_agent.cli.assembly.
from code_agent.prompts import (
    GENERAL_SUBAGENT_PROMPT,
    MAIN_SYSTEM_PROMPT as _MAIN_SYSTEM_PROMPT,
    SUBAGENT_PROMPTS,
)
_GENERAL_SUBAGENT_PROMPT = GENERAL_SUBAGENT_PROMPT


# ─── TUI (production Evil Code interface) ─────────────────────────────────────


async def _run_tui(
    args: argparse.Namespace,
    session: Session,
    phase2: dict,
    config: ProviderConfig,
) -> int:
    """Launch the production Evil Code TUI with a provider-reload loop.

    When the TUI's provider settings screen saves a new profile and asks to use
    it, the app exits with ``TUI_RELOAD_PROVIDER_SETTINGS``; this loop reloads
    the managed profile and relaunches the TUI with the new provider.
    """
    from code_agent.tui.adapters import (
        InstrumentedAgent,
        InteractionModeController,
        ReasoningEffortController,
        RunnerBinding,
        TuiAgentRunner,
        TuiController,
        to_domain_items,
    )
    from code_agent.tui.domain import SessionApprovalBroker, SessionSummary
    from code_agent.tui.stores import (
        HttpProviderCatalog,
        JsonProviderSettingsStore,
        JsonUiPreferencesStore,
    )
    from code_agent.tui.tui import (
        TUI_RELOAD_PROVIDER_SETTINGS,
        EvilCodeApp,
        ProviderSetupApp,
    )
    # Assembly imports deferred for startup speed (see _async_main comment).
    from code_agent.cli.assembly import (
        open_agent_runtime,
        sandbox_gc_loop,
    )
    from code_agent.core.session import Session

    cwd_abs = os.path.abspath(args.cwd)
    provider_store = JsonProviderSettingsStore()
    provider_catalog = HttpProviderCatalog()
    ui_preferences = JsonUiPreferencesStore()
    session_store = phase2.get("session_store")
    permission_manager = phase2.get("permission_mgr")
    plan_tracker = phase2.get("plan_tracker")
    skill_manager = phase2.get("skill_manager")

    current_session = session
    current_session_id = getattr(current_session, "_session_id", None)
    initial_items = to_domain_items(current_session.messages)

    async with AsyncExitStack() as runtimes:
        while True:
            settings = await provider_store.load()
            selected_profile = settings.default_provider
            managed_profile = (
                settings.profile(selected_profile)
                if selected_profile is not None
                else None
            )
            tui_config = (
                config_from_profile(managed_profile)
                if managed_profile is not None
                else config
            )

            if not tui_config.api_key:
                # No ready credential yet: first-run provider wizard.
                setup = ProviderSetupApp(
                    provider_settings=settings,
                    provider_settings_store=provider_store,
                    provider_catalog=provider_catalog,
                    language=await ui_preferences.load_language(),
                    first_run=not settings.profiles,
                    initial_profile=(
                        selected_profile
                        if selected_profile and settings.profile(selected_profile)
                        else None
                    ),
                )
                configured = await setup.run_async()
                if not configured:
                    return 0
                continue

            profile_name = selected_profile or "default"
            model_name = tui_config.model
            broker = SessionApprovalBroker()

            async def make_binding(cfg, *, ca_session, session_id, items) -> RunnerBinding:
                runtime = await runtimes.enter_async_context(open_agent_runtime(
                    args, ca_session, cfg,
                    phase2_modules=phase2, trace=trace,
                    client_factory=lambda: build_resilient_client(cfg, oauth_refresh_cb),
                    agent_factory=InstrumentedAgent,
                    agent_options={
                        "permission_manager": permission_manager,
                        "approval_broker": broker,
                        "session_id": session_id,
                        "provider_name": profile_name,
                        "model_name": cfg.model,
                    },
                ))
                return RunnerBinding(
                    TuiAgentRunner(runtime.agent, session_id=session_id), items=items,
                )

            binding = await make_binding(
                tui_config,
                ca_session=current_session,
                session_id=current_session_id,
                items=initial_items,
            )

            async def resolve_provider(name: str) -> RunnerBinding:
                profile = (await provider_store.load()).profile(name)
                if profile is None:
                    raise ValueError(f"unknown provider profile: {name}")
                fresh = Session(system_prompt=_MAIN_SYSTEM_PROMPT)
                return await make_binding(
                    config_from_profile(profile),
                    ca_session=fresh,
                    session_id=None,
                    items=(),
                )

            async def resolve_session(session_id: str) -> RunnerBinding:
                if session_store is None:
                    raise ValueError("session persistence is disabled")
                meta, messages = await session_store.load_session(session_id, cwd_abs)
                if meta is None and not messages:
                    raise ValueError(f"session '{session_id}' not found")
                ca_session = Session.from_dicts(messages)
                ca_session._session_id = session_id
                items = to_domain_items(ca_session.messages)
                return await make_binding(
                    tui_config,
                    ca_session=ca_session,
                    session_id=session_id,
                    items=items,
                )

            async def list_sessions():
                if session_store is None:
                    return ()
                summaries = []
                for meta in session_store.list_sessions():
                    if meta.cwd != cwd_abs:
                        continue
                    summaries.append(SessionSummary(
                        id=meta.session_id,
                        cwd=meta.cwd,
                        provider=meta.model_id,
                        model=meta.model_id,
                        created_at=meta.created_at,
                        updated_at=meta.updated_at,
                        title=meta.title or None,
                    ))
                return tuple(summaries)

            async def rename_session(session_id: str, title: str) -> SessionSummary:
                if session_store is None:
                    raise ValueError("session persistence is disabled")
                await session_store.update_meta(session_id, cwd_abs, {"title": title})
                meta, _ = await session_store.load_session(session_id, cwd_abs)
                if meta is None:
                    raise ValueError(f"session '{session_id}' not found")
                return SessionSummary(
                    id=meta.session_id,
                    cwd=meta.cwd,
                    provider=meta.model_id,
                    model=meta.model_id,
                    created_at=meta.created_at,
                    updated_at=meta.updated_at,
                    title=meta.title or None,
                )

            reasoning_effort = await ui_preferences.load_reasoning_effort()
            interaction_mode = await ui_preferences.load_interaction_mode()

            reasoning = ReasoningEffortController(
                ui_preferences=ui_preferences, initial=reasoning_effort,
            )
            mode_controller = InteractionModeController(
                plan_tracker=plan_tracker,
                permission_manager=permission_manager,
                ui_preferences=ui_preferences,
                initial=interaction_mode,
            )
            controller = TuiController(
                runner=binding.runner,
                reasoning=reasoning,
                interaction_mode=mode_controller,
                provider_store=provider_store,
                managed_settings=settings,
                selected_profile=profile_name,
                provider_name=profile_name,
                model_name=model_name,
                resolve_provider=resolve_provider,
                list_sessions=list_sessions,
                rename_session=rename_session,
                resolve_session=resolve_session,
            )
            from code_agent.sandbox.draft import DraftManager

            app = EvilCodeApp(
                controller,
                approval_controller=broker,
                provider_controller=controller,
                reasoning_controller=controller,
                interaction_mode_controller=controller,
                session_controller=controller,
                task_controller=None,
                session_task_controller=None,
                plan_controller=None,
                ui_preferences=ui_preferences,
                provider_settings_store=provider_store,
                provider_catalog=provider_catalog,
                managed_provider_settings=settings,
                language=await ui_preferences.load_language(),
                initial_items=initial_items,
                provider_name=profile_name,
                model_name=model_name,
                cwd=Path(cwd_abs),
                reasoning_effort=reasoning_effort,
                interaction_mode=interaction_mode,
                sandbox_label=(
                    f"sandbox:{phase2['sandbox_mgr'].mode}"
                    if phase2.get("sandbox_mgr") is not None
                    else None
                ),
                sandbox_manager=phase2.get("sandbox_mgr"),
                draft_manager=DraftManager(),
            )
            sandbox_mgr = phase2.get("sandbox_mgr")
            gc_task = None
            if sandbox_mgr is not None:
                gc_task = asyncio.create_task(sandbox_gc_loop(sandbox_mgr))
            try:
                await app.run_async()
            finally:
                if gc_task is not None:
                    from code_agent.runtime import stop_background_task
                    await stop_background_task(gc_task)
            return_code = app.return_code if app.return_code is not None else 0
            if return_code != TUI_RELOAD_PROVIDER_SETTINGS:
                return return_code
            # Provider settings changed: start a fresh conversation next iteration.
            current_session = Session(system_prompt=_MAIN_SYSTEM_PROMPT)
            current_session_id = None
            initial_items = ()


# ─── Entry Point ───────────────────────────────────────────────────────────────


async def _async_main(args: argparse.Namespace) -> None:
    """Async portion of main: session resume + agent execution."""
    # Deferred imports: assembly + execution are heavy (pull in tools/hooks/core).
    # Kept deferred so `code-agent --help` stays fast.
    from code_agent.cli.assembly import (
        activate_plan_mode,
        open_agent_runtime,
        close_phase2,
        init_phase2_modules,
        inject_available_skills,
        resolve_session,
    )
    from code_agent.cli.execution import run_headless, run_interactive
    from code_agent.runtime import close_resource

    # Resolve session (new or resumed)
    session, phase2 = await resolve_session(args)

    # Init Phase 2 modules (may extend phase2 dict)
    phase2.update(init_phase2_modules(args))

    async with AsyncExitStack() as resources:
        resources.push_async_callback(close_phase2, phase2.get("memory_index"), trace)
        # [Phase 7] 2026-08-05: Sandbox manager — 项目级环境 + 自动路由
        sandbox_mgr = None
        try:
            from code_agent.sandbox.manager import SandboxManager, resolve_sandbox_mode

            sandbox_mode = resolve_sandbox_mode(getattr(args, "sandbox", False))
            if sandbox_mode != "off":
                sandbox_mgr = SandboxManager(
                    mode=sandbox_mode,
                    cwd=os.path.abspath(getattr(args, "cwd", ".")),
                    profile=getattr(args, "sandbox_profile", None),
                )
                resources.push_async_callback(close_resource, sandbox_mgr)
                phase2["sandbox_mgr"] = sandbox_mgr
                permission_mgr = phase2.get("permission_mgr")
                if permission_mgr is not None and hasattr(
                    permission_mgr, "configure_sandbox"
                ):
                    permission_mgr.configure_sandbox(
                        sandbox_mode, sandbox_mgr.is_available
                    )
        except Exception:
            logger.warning("Failed to init SandboxManager", exc_info=True)

        # Open memory index if available
        memory_index = phase2.get("memory_index")
        if memory_index is not None:
            try:
                await memory_index.open()
            except Exception:
                logger.warning("Failed to open MemoryIndex", exc_info=True)
                phase2.pop("memory_index", None)

        # [Phase 3] 2026-07-27: Load skills if skill manager is available
        skill_manager = phase2.get("skill_manager")
        if skill_manager is not None:
            try:
                await skill_manager.load_all()
            except Exception:
                logger.warning("Failed to load skills", exc_info=True)
                phase2.pop("skill_manager", None)

        # [Phase 3] 2026-08-04: inject the available-skills list so the model can
        # discover skills instead of finding them only by trial and error.
        if skill_manager is not None:
            inject_available_skills(session, skill_manager)

        # [Phase 4] 2026-08-04: --plan activates Plan Mode for headless/REPL too
        activate_plan_mode(args, phase2)

        # [TUI] Production Evil Code TUI with provider-reload loop
        if args.tui:
            await _run_tui(args, session, phase2, config)
            return

        if sandbox_mgr is not None:
            resources.push_async_callback(close_resource, sandbox_mgr, method="gc")

        # [Phase 4] 2026-08-04: optional MCP server connection
        # (--mcp-server NAME=COMMAND for stdio, or NAME=https://... for HTTP)
        mcp_client = None
        mcp_spec = getattr(args, "mcp_server", None)
        if mcp_spec:
            try:
                from code_agent.mcp.client import McpClient, McpServerConfig

                name, _, spec = mcp_spec.partition("=")
                name = name.strip() or "mcp"
                spec = spec.strip()
                is_url = spec.startswith(("http://", "https://"))
                mcp_client = McpClient(
                    McpServerConfig(
                        name=name,
                        url=spec if is_url else None,
                        command=shlex.split(spec) if not is_url else None,
                    )
                )
                resources.push_async_callback(close_resource, mcp_client)
                await mcp_client.connect()
                phase2["mcp_client"] = mcp_client
                logger.info("MCP server '%s' connected", name)
            except Exception as exc:
                logger.warning("Failed to connect MCP server: %s", exc)
                mcp_client = None

        async with open_agent_runtime(
            args, session, config, phase2_modules=phase2, trace=trace,
            client_factory=lambda: build_resilient_client(config, oauth_refresh_cb),
        ) as runtime:
            if args.prompt:
                await run_headless(
                    runtime.agent, args.prompt,
                    output_format=getattr(args, "output_format", "plain"),
                )
            else:
                await run_interactive(
                    runtime.agent, args, runtime.client, runtime.tools,
                    phase2_modules=phase2,
                )


# Module-level variables set by main() before _async_main()
config: ProviderConfig
trace: TraceWriter | None


def main() -> None:
    """CLI entry point with argparse."""
    global config, trace

    # Subcommand dispatch (config/sandbox/draft extracted to cli.subcommands)
    if len(sys.argv) > 1 and sys.argv[1] == "config":
        from code_agent.cli.subcommands.config import run_config_command
        run_config_command(sys.argv[2:])
        return

    if len(sys.argv) > 1 and sys.argv[1] == "sandbox":
        from code_agent.cli.subcommands.sandbox import run_sandbox_command
        run_sandbox_command(sys.argv[2:])
        return

    if len(sys.argv) > 1 and sys.argv[1] == "draft":
        from code_agent.cli.subcommands.draft import run_draft_command
        run_draft_command(sys.argv[2:])
        return

    parser = build_parser()
    args = parser.parse_args()

    # Change working directory
    if args.cwd != ".":
        cwd = os.path.expanduser(args.cwd)
        if not os.path.isdir(cwd):
            print(f"Error: directory not found: {cwd}", file=sys.stderr)
            sys.exit(1)
        os.chdir(cwd)

    # Resolve auth
    config = resolve_provider_config(args)

    # Trace
    trace = TraceWriter() if args.trace else None
    if trace:
        trace.open()
        trace.event("auth_resolve",
                    backend=config.api_backend.value,
                    model=config.model,
                    base_url=config.base_url,
                    auth_scheme=config.auth_scheme.value)

    # Run async main
    try:
        asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)


if __name__ == "__main__":
    main()
