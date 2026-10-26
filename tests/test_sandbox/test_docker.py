# [Phase 4] 2026-07-29: Tests for sandbox/docker_exec.py
"""Tests for DockerSandbox."""

import pytest
from unittest.mock import AsyncMock, Mock

from code_agent.sandbox.docker_exec import DockerSandbox


class TestDockerSandbox:
    """Test DockerSandbox (limited — Docker may not be available)."""

    @pytest.mark.asyncio
    async def test_is_available_does_not_raise(self):
        """is_available() should not raise even if Docker is missing."""
        sandbox = DockerSandbox()
        result = await sandbox.is_available()
        # Just verify it returns a boolean (True if Docker installed, False otherwise)
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_execute_without_docker_returns_error(self, monkeypatch):
        """execute() returns error if Docker is not installed."""
        sandbox = DockerSandbox()
        # Always exercise the absent executable case, regardless of host setup.
        start = AsyncMock(side_effect=FileNotFoundError)
        monkeypatch.setattr("asyncio.create_subprocess_exec", start)
        assert await sandbox.is_available() is False
        result = await sandbox.execute("echo hello", timeout=5)
        assert result.is_error
        assert "error" in result.output.lower() or "not installed" in result.output.lower()
        assert all(call.args[0] == "docker" for call in start.call_args_list)

    @pytest.mark.asyncio
    async def test_unavailable_daemon_preserves_failure_without_host_fallback(self, monkeypatch):
        stderr = b"failed to connect to the docker API: daemon unavailable"
        proc = Mock(returncode=1)
        proc.wait = AsyncMock(return_value=1)
        proc.communicate = AsyncMock(return_value=(b"", stderr))
        start = AsyncMock(return_value=proc)
        monkeypatch.setattr("asyncio.create_subprocess_exec", start)
        sandbox = DockerSandbox()
        assert await sandbox.is_available() is False
        result = await sandbox.execute("echo hello", timeout=5)
        assert result.is_error
        assert "exit 1" in result.output
        assert stderr.decode() in result.output
        assert start.call_args_list[0].args == ("docker", "info")
        assert start.call_args_list[1].args[:2] == ("docker", "run")
        assert ":/workspace:ro" in " ".join(start.call_args_list[1].args)
        assert start.await_count == 2

    def test_default_config(self):
        """Default configuration is set correctly."""
        sandbox = DockerSandbox()
        assert sandbox._image == "python:3.12-slim"
        assert sandbox._network == "none"
        assert sandbox._memory == "512mb"
        assert sandbox._cpus == "1"

    def test_custom_config(self):
        """Custom configuration is accepted."""
        sandbox = DockerSandbox(
            image="ubuntu:22.04",
            memory="1gb",
            cpus="2",
        )
        assert sandbox._image == "ubuntu:22.04"
        assert sandbox._memory == "1gb"
        assert sandbox._cpus == "2"
