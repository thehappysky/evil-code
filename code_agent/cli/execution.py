"""Execution modes for the CLI: headless, interactive REPL, session listing.

Extracted from ``cli/main.py`` (M3 prep). These are the three user-facing run
modes invoked by ``_async_main`` after agent assembly.

``build_agent`` and ``get_data_dir`` live in ``code_agent.cli.assembly``; they
are imported at module top alongside the rest of the ``code_agent.cli.*`` deps.
"""

from __future__ import annotations

import argparse
import os
import sys

from code_agent.api.client import ChatClient
from code_agent.cli.assembly import build_agent, get_data_dir
from code_agent.cli.headless import run_headless_json, run_headless_streaming
from code_agent.core.agent import Agent
from code_agent.core.exceptions import AgentError, AuthenticationError, MaxTurnsReached
from code_agent.core.session import Session
from code_agent.prompts import MAIN_SYSTEM_PROMPT
from code_agent.storage.session_store import SessionStore
from code_agent.tools.registry import ToolRegistry


# ─── Execution Modes ───────────────────────────────────────────────────────────


async def run_headless(
    agent: Agent, prompt: str, output_format: str = "plain",
) -> None:
    """Single prompt mode with configurable output format.

    Args:
        agent: The Agent instance.
        prompt: User prompt text.
        output_format: "plain" (default), "json", or "streaming-json".
    """
    try:
        if output_format == "json":
            await run_headless_json(agent, prompt)
        elif output_format == "streaming-json":
            await run_headless_streaming(agent, prompt)
        else:
            # Plain text (original behavior)
            async for event in agent.run(prompt):
                if event.kind == "text_delta" and event.text:
                    print(event.text, end="", flush=True)
                elif event.kind == "error" and event.text:
                    print(f"\n[Error] {event.text}", file=sys.stderr)
            print()  # Final newline
    except MaxTurnsReached as exc:
        print(f"\n[Stopped] {exc}", file=sys.stderr)
        sys.exit(1)
    except AuthenticationError as exc:
        print(f"\n[Auth Error] {exc}", file=sys.stderr)
        sys.exit(1)


async def run_interactive(
    agent: Agent,
    args: argparse.Namespace,
    client: ChatClient,
    tools: ToolRegistry,
    phase2_modules: dict | None = None,
) -> None:
    """REPL mode: read input -> agent.run() -> print results.

    /clear rebuilds the Agent with a fresh Session.
    /sessions lists saved sessions.
    """
    print("code-agent REPL (type 'exit' or Ctrl+D to quit)")
    print("-" * 50)

    while True:
        try:
            user_input = input("\n> ")
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
            break

        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            print("Bye.")
            break

        if user_input.lower() == "/clear":
            session = Session(system_prompt=MAIN_SYSTEM_PROMPT)
            agent = build_agent(args, session, client, tools,
                                 phase2_modules=phase2_modules)
            print("Session reset.")
            continue

        if user_input.lower() == "/sessions":
            list_sessions(args)
            continue

        try:
            async for event in agent.run(user_input):
                if event.kind == "text_delta" and event.text:
                    print(event.text, end="", flush=True)
                elif event.kind == "tool_call_delta" and event.tool_call:
                    if event.tool_call.name:
                        print(
                            f"\n  [tool: {event.tool_call.name}]",
                            end="", flush=True,
                        )
                elif event.kind == "error" and event.text:
                    print(f"\n[Error] {event.text}", file=sys.stderr)
            print()  # Final newline after response
        except MaxTurnsReached as exc:
            print(f"\n[Stopped] {exc}", file=sys.stderr)
        except AuthenticationError as exc:
            print(f"\n[Auth Error] {exc}", file=sys.stderr)
            break
        except AgentError as exc:
            print(f"\n[Agent Error] {exc}", file=sys.stderr)


def list_sessions(args: argparse.Namespace) -> None:
    """List saved sessions for the current working directory."""
    try:
        store = SessionStore(root_dir=get_data_dir() / "sessions")
        sessions = store.list_sessions()

        # Filter by cwd
        cwd = os.path.abspath(args.cwd)
        matching = [s for s in sessions if s.cwd == cwd]

        if not matching:
            print("No saved sessions for current directory.")
            return

        print(f"\nSaved sessions for {cwd}:")
        print("-" * 60)
        for s in matching:
            ts = s.updated_at.strftime("%Y-%m-%d %H:%M") if s.updated_at else "unknown"
            print(
                f"  {s.session_id[:20]:20s}  {ts}  "
                f"turns={s.prompt_count:3d}  "
                f"tokens={s.total_tokens:5d}  "
                f"{s.title or '(untitled)'}"
            )
        print("-" * 60)
        print(f"Total: {len(matching)} session(s)")
        print("Use --resume <id> to resume, or --continue for latest.")

    except Exception as exc:
        print(f"Failed to list sessions: {exc}", file=sys.stderr)
