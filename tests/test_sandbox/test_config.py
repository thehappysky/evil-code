# [Phase 7] 2026-08-06: sandbox.toml 配置加载测试。
"""Tests for code_agent/sandbox/config.py."""

from __future__ import annotations

from pathlib import Path

from code_agent.sandbox.config import load_sandbox_toml, resolve_egress_mode


def test_global_and_project_config_merge(tmp_path, monkeypatch):
    global_dir = tmp_path / "home" / ".code-agent"
    global_dir.mkdir(parents=True)
    (global_dir / "sandbox.toml").write_text(
        "[sandbox]\n"
        "max_projects = 20\n"
        'image = "mirror.example.com/python:3.12-slim"\n'
        'egress_extra = ["global.example.com"]\n',
        encoding="utf-8",
    )
    project = tmp_path / "proj"
    (project / ".code-agent").mkdir(parents=True)
    (project / ".code-agent" / "sandbox.toml").write_text(
        "[sandbox]\n"
        "disk_warning_gb = 5\n"
        'egress_extra = ["artifacts.local"]\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(Path, "home", staticmethod(lambda: global_dir.parent))
    cfg = load_sandbox_toml(str(project))

    assert cfg.max_projects == 20
    assert cfg.disk_warning_gb == 5.0
    assert cfg.image == "mirror.example.com/python:3.12-slim"
    assert "global.example.com" in cfg.egress_extra
    assert "artifacts.local" in cfg.egress_extra


def test_missing_config_gives_defaults(tmp_path):
    cfg = load_sandbox_toml(str(tmp_path / "nonexistent"))
    assert cfg.max_projects is None
    assert cfg.disk_warning_gb is None
    assert cfg.profile is None
    assert cfg.profiles == {}
    assert cfg.egress_extra == []


def test_resolve_egress_mode(monkeypatch):
    monkeypatch.delenv("CODE_AGENT_SANDBOX_EGRESS", raising=False)
    assert resolve_egress_mode() == "allowlist"
    assert resolve_egress_mode("none") == "none"
    assert resolve_egress_mode("open") == "open"
    assert resolve_egress_mode("bogus") == "allowlist"
    monkeypatch.setenv("CODE_AGENT_SANDBOX_EGRESS", "none")
    assert resolve_egress_mode() == "none"


def test_profiles_load_and_global_wins(tmp_path, monkeypatch):
    global_dir = tmp_path / "home" / ".code-agent"
    global_dir.mkdir(parents=True)
    (global_dir / "sandbox.toml").write_text(
        "[sandbox]\n"
        'profile = "ci"\n'
        "[sandbox.profiles.ci]\n"
        'extends = "workspace"\n'
        'read_only = ["C:/global-ro"]\n'
        "restrict_network = true\n"
        "[sandbox.profiles.local]\n"
        'read_write = ["C:/local-rw"]\n',
        encoding="utf-8",
    )
    project = tmp_path / "proj"
    (project / ".code-agent").mkdir(parents=True)
    # 项目想覆盖全局 ci → 被忽略；新增 local → 生效
    (project / ".code-agent" / "sandbox.toml").write_text(
        "[sandbox.profiles.ci]\n"
        'read_only = ["C:/evil-override"]\n'
        "restrict_network = false\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(Path, "home", staticmethod(lambda: global_dir.parent))
    cfg = load_sandbox_toml(str(project))

    assert cfg.profile == "ci"
    assert cfg.profiles["ci"].read_only == ["C:/global-ro"]
    assert cfg.profiles["ci"].restrict_network is True
    assert cfg.profiles["local"].read_write == ["C:/local-rw"]
