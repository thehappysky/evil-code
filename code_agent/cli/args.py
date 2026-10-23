"""CLI argument parser definition.

Extracted from ``cli/main.py`` (M3 prep): the argparse parser is built here so
``main()`` stays focused on subcommand dispatch + dependency assembly.

Subcommand dispatch via ``sys.argv`` peeks (config / sandbox / draft) stays in
``main()`` unchanged — M3 will migrate those to subparsers.
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level ``code-agent`` argparse parser.

    Returns:
        Configured ArgumentParser with all CLI flags registered.
    """
    parser = argparse.ArgumentParser(
        prog="code-agent",
        description="Python AI Coding Agent - think, act, observe",
    )
    parser.add_argument(
        "prompt", nargs="?", default=None,
        help="Prompt for headless mode (omit for interactive REPL)",
    )
    parser.add_argument(
        "--cwd", default=".",
        help="Working directory (default: current directory)",
    )
    parser.add_argument(
        "--model", default="deepseek-v4-pro",
        help="Model ID (default: deepseek-v4-pro)",
    )
    parser.add_argument(
        "--max-turns", type=int, default=20,
        help="Maximum reasoning turns per prompt (default: 20)",
    )
    parser.add_argument(
        "--base-url", default="https://api.deepseek.com/anthropic",
        help="API base URL",
    )
    parser.add_argument(
        "--api-key", default=None,
        help="API key (bypasses env vars, settings.json, and OAuth)",
    )
    parser.add_argument(
        "--provider", default="auto",
        choices=["auto", "chat_completions", "messages", "responses"],
        help="API backend protocol (default: auto-infer from base-url)",
    )
    parser.add_argument(
        "--tui", action="store_true", default=False,
        help="Launch Textual TUI (full-screen interactive mode)",
    )
    parser.add_argument(
        "--trace", action="store_true", default=False,
        help="Write structured JSONL audit trace to ~/.evil-code/traces/",
    )
    # Phase 2: Session resume
    parser.add_argument(
        "--resume", default=None,
        help="Resume a previous session by ID",
    )
    parser.add_argument(
        "--continue", action="store_true", dest="continue_session",
        default=False,
        help="Resume the latest session for the current directory",
    )
    # Phase 2: Feature toggles
    parser.add_argument(
        "--no-memory", action="store_true", default=False,
        help="Disable cross-session memory injection",
    )
    parser.add_argument(
        "--no-compaction", action="store_true", default=False,
        help="Disable auto-compaction",
    )
    parser.add_argument(
        "--no-persist", action="store_true", default=False,
        help="Disable session persistence",
    )
    # [Phase 3] 2026-07-27: New CLI flags
    parser.add_argument(
        "--output-format", default="plain",
        choices=["plain", "json", "streaming-json"],
        help="Headless output format (default: plain). "
             "json: single JSON object after completion. "
             "streaming-json: NDJSON stream.",
    )
    parser.add_argument(
        "--no-skills", action="store_true", default=False,
        help="Disable skill discovery and SkillTool",
    )
    parser.add_argument(
        "--plan", action="store_true", default=False,
        help="Start in Plan Mode (plan-first-then-code workflow)",
    )
    # [Phase 4] 2026-07-29: New CLI flags
    parser.add_argument(
        "--yolo", action="store_true", default=False,
        help="YOLO mode: auto-approve all tool calls without confirmation",
    )
    parser.add_argument(
        "--allow", action="append", dest="allow_rules", default=None,
        help="Allow rule pattern (repeatable). "
             "e.g.: --allow 'Bash(npm*)' --allow 'Edit(**/*.py)'",
    )
    parser.add_argument(
        "--deny", action="append", dest="deny_rules", default=None,
        help="Deny rule pattern (repeatable). "
             "e.g.: --deny 'Bash(rm*)'",
    )
    parser.add_argument(
        "--sandbox", action="store_true", default=False,
        help="Execute shell commands in Docker sandbox",
    )
    parser.add_argument(
        "--sandbox-profile", default=None,
        help="Sandbox profile name from sandbox.toml "
             "(or CODE_AGENT_SANDBOX_PROFILE env)",
    )
    parser.add_argument(
        "--no-plugins", action="store_true", default=False,
        help="Disable plugin discovery and loading",
    )
    parser.add_argument(
        "--mcp-server", default=None,
        help="Connect an MCP server and expose its tools to the model. "
             "Syntax: NAME=COMMAND (stdio) or NAME=https://host (HTTP). "
             "Repeatable? Use once per server for now.",
    )
    return parser
