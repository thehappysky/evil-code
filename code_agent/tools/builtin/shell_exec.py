"""shell_exec tool - Execute shell commands with timeout.

Logic reference: Rust version BashTool
(implementations/grok_build/bash/mod.rs).

Features:
- shlex.split() for safe command parsing
- asyncio.create_subprocess_exec() for non-blocking execution
- asyncio.wait_for() timeout -> proc.kill()
- stdout + stderr merged, non-zero exit code -> is_error=True
"""

from __future__ import annotations

import asyncio
import os
import shlex
import sys
from typing import TYPE_CHECKING, Awaitable, Callable

from pydantic import BaseModel, Field

from code_agent.tools.result import ToolResult

if TYPE_CHECKING:
    # Type-only import: tools/ must not take a runtime dependency on sandbox/
    # (the sandbox layer consumes tools, not the reverse). The SandboxManager
    # type is only used in annotations, which `from __future__ import
    # annotations` keeps as strings at runtime.
    from code_agent.sandbox.manager import SandboxManager


class ShellInput(BaseModel):
    """Input for shell_exec tool."""

    command: str = Field(description="Shell command to execute")
    timeout: int = Field(default=120, description="Timeout in seconds (max 600)")
    workdir: str | None = Field(default=None, description="Working directory for the command")


async def shell_exec(input: ShellInput) -> ToolResult:
    """Execute a shell command and return its output.

    Args:
        input: ShellInput with command, timeout, workdir.

    Returns:
        ToolResult with combined stdout+stderr output.
    """
    timeout = min(max(input.timeout, 1), 600)  # Clamp to [1, 600]
    workdir = input.workdir

    if workdir:
        workdir = os.path.expanduser(workdir)
        if not os.path.isdir(workdir):
            return ToolResult.error(f"Working directory not found: {workdir}")

    # Use shell=True on Windows for proper command handling,
    # shlex parsing on POSIX
    try:
        if sys.platform == "win32":
            proc = await asyncio.create_subprocess_shell(
                input.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
            )
        else:
            # Try shlex split first for safety
            try:
                args = shlex.split(input.command)
            except ValueError:
                # If shlex fails, fall back to shell
                args = input.command

            if isinstance(args, list):
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=workdir,
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=workdir,
                )
    except OSError as exc:
        return ToolResult.error(f"Failed to start process: {exc}")

    # Wait with timeout
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ToolResult.error(
            f"Command timed out after {timeout}s and was killed.\n"
            f"Command: {input.command[:200]}"
        )

    # Decode output
    stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
    stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""

    # Combine output
    parts = []
    if stdout_text:
        parts.append(stdout_text)
    if stderr_text:
        parts.append(f"[stderr]\n{stderr_text}")

    output = "\n".join(parts) if parts else "(no output)"

    # Add exit code info
    exit_code = proc.returncode
    if exit_code != 0:
        output += f"\n\n[Exit code: {exit_code}]"
        return ToolResult.error(output)

    return ToolResult.ok(output)


# ─── Sandbox-routed factory ──────────────────────────────────────────────────
# [INT-05] 2026-08-09: Moved the inline `shell_exec_routed` closure out of
# `cli/assembly.py::build_tools()`. The shell_exec routing decision (safe →
# host, risky → Docker sandbox) is a tools-layer concern and belongs with the
# shell_exec tool itself, not in the CLI assembly root. `cli/assembly.py` now
# calls this factory; the function name is preserved so existing tests that
# assert `handler.__name__ == "shell_exec_routed"` keep passing.


def make_shell_exec_routed(
    sandbox_manager: "SandboxManager",
    allow_fallback: bool = True,
) -> Callable[[ShellInput], Awaitable[ToolResult]]:
    """Build a shell_exec handler that auto-routes to the sandbox.

    Safe commands run on the host via the sandbox manager's own routing;
    risky commands run inside the Docker sandbox. When a sandboxed command
    fails and ``allow_fallback`` is True, execution falls back to the host.

    Args:
        sandbox_manager: Routes the command (host vs. container).
        allow_fallback: If True, permit host fallback on sandbox failure.

    Returns:
        An async handler ``shell_exec_routed(input: ShellInput) -> ToolResult``
        bound to the given sandbox manager.
    """

    async def shell_exec_routed(input: ShellInput) -> ToolResult:
        """Execute a shell command with sandbox auto-routing."""
        return await sandbox_manager.run(
            input.command,
            workdir=input.workdir,
            timeout=input.timeout,
            allow_fallback=allow_fallback,
        )

    return shell_exec_routed
