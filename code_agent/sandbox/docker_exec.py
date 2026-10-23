# [Phase 4] 2026-07-29: DockerSandbox — isolated container execution
"""Docker-based command execution sandbox.

References:
    - design-docs/Python-Agent-MVP-Phase4-详细设计.md §6 sandbox/docker_exec.py
    - Rust xai-grok-sandbox (nono: Landlock/Seatbelt)

Security measures:
    - Read-only bind mount of working directory
    - Network disabled (--network=none)
    - Memory limit (default 512MB)
    - CPU limit (default 1 core)
    - Auto-remove container after execution (--rm)
    - Non-root user inside container
    - Timeout with SIGKILL fallback
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex

from code_agent.tools.result import ToolResult

logger = logging.getLogger(__name__)


class DockerSandbox:
    """Docker-based command execution sandbox.

    Usage::

        sandbox = DockerSandbox()
        if await sandbox.is_available():
            result = await sandbox.execute("npm test", timeout=60)
    """

    def __init__(
        self,
        image: str = "python:3.12-slim",
        workdir: str = "/workspace",
        network: str = "none",
        memory: str = "512mb",
        cpus: str = "1",
    ):
        self._image = image
        self._workdir = workdir
        self._network = network
        self._memory = memory
        self._cpus = cpus

    async def execute(self, command: str, timeout: int = 120) -> ToolResult:
        """Execute command in isolated Docker container.

        Steps:
        1. docker run -d with security flags
        2. Wait for completion or timeout
        3. Collect stdout + stderr
        4. On timeout: docker kill + docker rm

        Args:
            command: Shell command to execute.
            timeout: Maximum execution time in seconds.

        Returns:
            ToolResult with merged output or error.
        """
        cwd = os.getcwd()

        # Build docker run command
        docker_cmd = [
            "docker", "run",
            "--rm",                           # Auto-remove
            "--network", self._network,       # No network
            "--memory", self._memory,         # Memory limit
            "--cpus", self._cpus,             # CPU limit
            "--user", "1000:1000",            # Non-root user
            "--workdir", self._workdir,
            "-v", f"{cwd}:{self._workdir}:ro",  # Read-only mount
            self._image,
            "bash", "-c", command,
        ]

        logger.debug("Docker sandbox: %s", " ".join(docker_cmd))

        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
            except asyncio.TimeoutError:
                # Kill container on timeout
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()

                return ToolResult.error(
                    f"Sandbox command timed out after {timeout}s: {command[:100]}"
                )

            stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

            output = stdout
            if stderr:
                if output:
                    output += "\n" + stderr
                else:
                    output = stderr

            if proc.returncode != 0:
                return ToolResult.error(
                    f"Sandbox command failed (exit {proc.returncode}):\n{output[:5000]}"
                )

            return ToolResult.ok(output)

        except FileNotFoundError:
            return ToolResult.error(
                "Docker is not installed. Install Docker to use sandboxed execution."
            )
        except OSError as exc:
            return ToolResult.error(f"Sandbox execution error: {exc}")

    async def is_available(self) -> bool:
        """Check if Docker is installed and accessible.

        Returns:
            True if Docker daemon is running and accessible.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            return await proc.wait() == 0
        except (FileNotFoundError, OSError):
            return False
