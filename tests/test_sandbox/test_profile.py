# [Phase 7] 2026-08-06: 自定义 profile 挂载 / deny / net 复用测试。
"""Tests for custom sandbox profiles and net container reuse."""

from __future__ import annotations

import pytest

from code_agent.sandbox.manager import SandboxManager


def _project_with_profile(tmp_path, toml_body: str) -> str:
    project = tmp_path / "proj"
    (project / ".code-agent").mkdir(parents=True)
    (project / ".code-agent" / "sandbox.toml").write_text(
        toml_body, encoding="utf-8"
    )
    (project / "demo.txt").write_text("x\n", encoding="utf-8")
    (project / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (project / "secrets").mkdir()
    (project / "secrets" / "key.pem").write_text("k", encoding="utf-8")
    return str(project)


def test_profile_mounts_and_deny_binds(tmp_path):
    project = _project_with_profile(
        tmp_path,
        "[sandbox]\n"
        'profile = "ci"\n'
        "[sandbox.profiles.ci]\n"
        'read_only = ["C:/shared-ro"]\n'
        'read_write = ["C:/shared-rw"]\n'
        'deny = [".env", "secrets"]\n'
        "restrict_network = true\n",
    )
    mgr = SandboxManager(
        mode="auto", cwd=project, state_path=tmp_path / "state.json",
    )
    assert mgr.status()["profile"] == "ci"

    argv = mgr._build_docker_argv(
        "echo hi", _host(project), "pid1", "work", "persistent",
    )
    joined = " ".join(argv)
    assert "type=tmpfs,target=/scratch" in joined
    assert "TMPDIR=/scratch" in joined
    assert "C:/shared-ro:/mnt/ro-0:ro" in joined
    assert "C:/shared-rw:/mnt/rw-0:rw" in joined
    # restrict_network=true → work 档也被强制无网络
    assert "--network none" in joined
    # deny：.env 与 secrets 都被占位绑定覆盖
    assert "/workspace/.env:ro" in joined
    assert "/workspace/secrets:ro" in joined


def _host(path: str) -> str:
    import os

    return os.path.abspath(path).replace("\\", "/")


@pytest.mark.asyncio
async def test_net_container_reuse_same_root_only(tmp_path):
    mgr = SandboxManager(mode="auto", cwd=tmp_path, state_path=tmp_path / "s.json")
    mgr._net_container = "code-agent-net-test"
    mgr._net_container_root = _host(str(tmp_path / "a"))
    assert await mgr._ensure_net_container(str(tmp_path / "b")) is False
    assert await mgr._ensure_net_container(str(tmp_path / "a")) is True


@pytest.mark.asyncio
async def test_net_tier_uses_reusable_container(monkeypatch, tmp_path):
    from code_agent.tools.result import ToolResult

    mgr = SandboxManager(mode="auto", cwd=tmp_path, state_path=tmp_path / "s.json")
    called = {}

    async def fake_available(self):
        return True

    async def fake_ensure_image(self):
        return None

    async def fake_ensure(self, cwd):
        called["ensure"] = True
        return True

    async def fake_run_net(self, command, cwd, timeout, tier, lifecycle, pid, reason):
        called["run_net"] = command
        return ToolResult(output="net-ok", meta={"tier": tier, "reason": reason})

    monkeypatch.setattr(SandboxManager, "is_available", fake_available)
    monkeypatch.setattr(SandboxManager, "_ensure_image", fake_ensure_image)
    monkeypatch.setattr(SandboxManager, "_ensure_net_container", fake_ensure)
    monkeypatch.setattr(SandboxManager, "_run_in_net_container", fake_run_net)

    result = await mgr.run("curl https://example.com", timeout=30)
    assert called.get("ensure") is True
    assert called.get("run_net") == "curl https://example.com"
    assert result.output == "net-ok"
