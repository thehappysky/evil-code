# [Phase 7] 2026-08-05: Tests for sandbox routing policy.
"""Tests for code_agent/sandbox/policy.py."""

from __future__ import annotations

import tempfile
from pathlib import Path

from code_agent.sandbox.policy import classify


def _local_cwd() -> str:
    return str(Path.cwd())


def test_safe_whitelist_runs_locally():
    decision = classify("ls -la", cwd=_local_cwd())
    assert decision.verdict == "local"
    assert decision.reason == "safe_whitelist"


def test_git_status_runs_locally():
    decision = classify("git status --short", cwd=_local_cwd())
    assert decision.verdict == "local"


def test_echo_runs_locally():
    decision = classify("echo hello", cwd=_local_cwd())
    assert decision.verdict == "local"


def test_sensitive_path_goes_to_clean():
    decision = classify("cat /etc/shadow", cwd=_local_cwd())
    assert decision.is_sandbox
    assert decision.tier == "clean"
    assert decision.reason == "sensitive_path"


def test_shell_script_goes_to_clean():
    decision = classify("bash ./install.sh", cwd=_local_cwd())
    assert decision.is_sandbox
    assert decision.tier == "clean"
    assert decision.reason == "script_shell_executor"


def test_inline_python_goes_to_clean():
    decision = classify("python -c \"print(1)\"", cwd=_local_cwd())
    assert decision.is_sandbox
    assert decision.tier == "clean"
    assert decision.reason == "script_inline_or_external"


def test_in_workspace_python_goes_to_work():
    decision = classify("python tests/test_demo.py", cwd=_local_cwd())
    assert decision.is_sandbox
    assert decision.tier == "work"
    assert decision.reason == "run_in_workspace"


def test_outside_workspace_python_goes_to_clean():
    decision = classify("python /tmp/downloaded/evil.py", cwd=_local_cwd())
    assert decision.is_sandbox
    assert decision.tier == "clean"
    assert decision.reason == "script_inline_or_external"


def test_package_install_goes_to_work():
    for command in (
        "pip install requests",
        "pip3 install -r requirements.txt",
        "npm install",
        "poetry install",
    ):
        decision = classify(command, cwd=_local_cwd())
        assert decision.is_sandbox, command
        assert decision.tier == "work", command
        assert decision.reason == "package_install", command


def test_network_tool_goes_to_net():
    decision = classify("curl https://example.com", cwd=_local_cwd())
    assert decision.is_sandbox
    assert decision.tier == "net"
    assert decision.reason == "network_tool"


def test_git_write_goes_to_work():
    decision = classify("git clone https://github.com/foo/bar.git", cwd=_local_cwd())
    assert decision.is_sandbox
    assert decision.tier == "work"
    assert decision.reason == "git_write"


def test_unknown_risky_goes_to_clean():
    decision = classify("rm -rf /tmp/probe", cwd=_local_cwd())
    assert decision.is_sandbox
    assert decision.tier == "clean"
    assert decision.reason == "unclassified_risky"


def test_temp_cwd_is_ephemeral():
    with tempfile.TemporaryDirectory() as tmp:
        decision = classify("python test.py", cwd=tmp)
        assert decision.lifecycle == "ephemeral"


def test_project_cwd_is_persistent():
    decision = classify("python test.py", cwd=_local_cwd())
    assert decision.lifecycle == "persistent"
