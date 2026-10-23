# [Phase 7] 2026-08-05: sandbox.toml 配置加载（全局 + 项目，项目只增不覆盖）。
"""Sandbox TOML configuration.

读取顺序：``~/.code-agent/sandbox.toml``（全局）→ ``<workspace>/.code-agent/sandbox.toml``
（项目）。项目配置可以追加 egress 白名单域名、覆盖标量上限；项目不能删除
全局已经声明的域名。

示例：:

    [sandbox]
    max_projects = 20
    disk_warning_gb = 15
    image = "docker.1ms.run/library/python:3.12-slim"
    egress_extra = ["artifacts.mycompany.com"]
    profile = "ci"

    [sandbox.profiles.ci]
    extends = "workspace"
    read_only = ["C:/shared-readonly"]
    read_write = ["C:/shared-rw"]
    deny = [".env", "secrets/"]
    restrict_network = true
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SandboxTomlConfig:
    max_projects: int | None = None
    disk_warning_gb: float | None = None
    image: str | None = None
    profile: str | None = None
    profiles: dict[str, ProfileToml] = field(default_factory=dict)
    egress_extra: list[str] = field(default_factory=list)


@dataclass
class ProfileToml:
    extends: str = "workspace"
    read_only: list[str] = field(default_factory=list)
    read_write: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    restrict_network: bool | None = None


def _profile_from_dict(raw: dict) -> ProfileToml:
    return ProfileToml(
        extends=str(raw.get("extends", "workspace")),
        read_only=[
            str(item) for item in (raw.get("read_only") or []) if str(item).strip()
        ],
        read_write=[
            str(item) for item in (raw.get("read_write") or []) if str(item).strip()
        ],
        deny=[str(item) for item in (raw.get("deny") or []) if str(item).strip()],
        restrict_network=raw.get("restrict_network"),
    )


def _load_file(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def load_sandbox_toml(workspace: str | None = None) -> SandboxTomlConfig:
    config = SandboxTomlConfig()

    global_path = Path.home() / ".code-agent" / "sandbox.toml"
    project_path = (
        Path(workspace).resolve() / ".code-agent" / "sandbox.toml"
        if workspace
        else None
    )

    for path in (global_path, project_path):
        if path is None or not path.exists():
            continue
        raw = _load_file(path)
        sandbox = raw.get("sandbox") or {}
        if config.max_projects is None and sandbox.get("max_projects"):
            config.max_projects = int(sandbox["max_projects"])
        if config.disk_warning_gb is None and sandbox.get("disk_warning_gb"):
            config.disk_warning_gb = float(sandbox["disk_warning_gb"])
        if config.image is None and sandbox.get("image"):
            config.image = str(sandbox["image"]).strip() or None
        if config.profile is None and sandbox.get("profile"):
            config.profile = str(sandbox["profile"]).strip() or None
        raw_profiles = sandbox.get("profiles") or {}
        if isinstance(raw_profiles, dict):
            for name, raw in raw_profiles.items():
                if isinstance(raw, dict):
                    # 全局已定义的 profile 项目不可覆盖（仿 grok 语义）
                    config.profiles.setdefault(str(name), _profile_from_dict(raw))
        extra = sandbox.get("egress_extra") or []
        if isinstance(extra, list):
            config.egress_extra.extend(
                str(item).strip() for item in extra if str(item).strip()
            )
    return config


def resolve_egress_mode(env_value: str | None = None) -> str:
    value = (env_value or os.environ.get("CODE_AGENT_SANDBOX_EGRESS", "")).strip().lower()
    return value if value in {"none", "allowlist", "open"} else "allowlist"


__all__ = [
    "ProfileToml",
    "SandboxTomlConfig",
    "load_sandbox_toml",
    "resolve_egress_mode",
]
