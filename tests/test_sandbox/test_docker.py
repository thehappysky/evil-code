# [Phase 4] 2026-07-29: Tests for sandbox/docker_exec.py
"""Tests for DockerSandbox."""

import pytest

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
    async def test_execute_without_docker_returns_error(self):
        """execute() returns error if Docker is not installed."""
        sandbox = DockerSandbox()
        # Don't actually test if Docker is available — just verify the result format
        available = await sandbox.is_available()
        if not available:
            result = await sandbox.execute("echo hello", timeout=5)
            assert result.is_error
            assert "error" in result.output.lower() or "not installed" in result.output.lower()

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
