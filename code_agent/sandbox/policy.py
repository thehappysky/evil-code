# [Phase 7] 2026-08-05: Sandbox routing policy — 把 shell 命令分类成
# local / clean / net / work，并给出 risk_reason 与生命周期类型。
"""Sandbox routing policy.

每条 shell 命令在进入权限层和 shell_exec 之前先经过分类器：

    local        → 安全白名单命令 + 无敏感路径参数，本机直接执行
    sandbox      → 需要进 Docker 沙箱，附带 tier：
                   clean  只读工作区 + 无网络（默认最安全）
                   net    只读工作区 + 白名单出站网络
                   work   只读工作区 + 可写 /env 卷 + 白名单出站网络

规则优先级（后写的规则不能覆盖先写的）：
1. 脚本执行器（SCRIPT_EXECUTORS）一律进沙箱，永不在本机执行；
2. 包安装器（pip/npm/...）→ work；
3. 联网工具（curl/wget/git 写子命令）→ net/work；
4. 安全白名单命令若带敏感路径参数 → clean；
5. 安全白名单命令且参数安全 → local；
6. 其余未分类风险命令 → clean（fail-safe）。
"""

from __future__ import annotations

import os
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path

from code_agent.permission.safe_commands import is_safe_command


# ── 执行器集合 ────────────────────────────────────────────────────────────────

# 一切脚本执行器：命中即进沙箱，永不本机执行（白名单也拦不住这条规则）。
SCRIPT_EXECUTORS: frozenset[str] = frozenset({
    # shell
    "bash", "sh", "zsh", "dash", "ash", "ksh",
    # 解释器
    "python", "python3", "py", "node", "perl", "ruby", "php", "lua",
    "powershell", "pwsh", "cmd",
    # 命令执行类
    "xargs", "eval", "source", "exec",
})

# shell 类脚本执行器：默认 clean（只读 + 无网）。
SHELL_EXECUTORS: frozenset[str] = frozenset({
    "bash", "sh", "zsh", "dash", "ash", "ksh", "cmd", "eval", "source",
})

# 解释器类执行器：工作区内脚本 → work；内联 / 工作区外 → clean。
INTERPRETER_EXECUTORS: frozenset[str] = frozenset({
    "python", "python3", "py", "node", "perl", "ruby", "php", "lua",
    "powershell", "pwsh",
})

INLINE_FLAGS: frozenset[str] = frozenset({"-c", "-e", "-Command", "-command"})

# 包安装器：需要网络 + 可写环境 → work。
PACKAGE_INSTALLERS: frozenset[str] = frozenset({
    "pip", "pip3", "uv", "poetry", "npm", "yarn", "pnpm", "cargo", "apt",
    "apt-get",
})

INSTALL_SUBCOMMANDS: frozenset[str] = frozenset({
    "install", "ci", "add", "i",
})

# 联网工具：默认 net（只读工作区 + /tmp 可写）。
NETWORK_TOOLS: frozenset[str] = frozenset({"curl", "wget"})

# git 非只读子命令：需要写工作区 + 网络 → work。
GIT_WRITE_SUBCOMMANDS: frozenset[str] = frozenset({
    "clone", "pull", "push", "fetch", "add", "commit", "merge", "rebase",
    "checkout", "switch", "restore", "reset", "stash", "tag", "remote", "init",
})

# 敏感路径参数：白名单命令也不能在本机直接读这些。
SENSITIVE_PATH_FRAGMENTS: tuple[str, ...] = (
    "/etc/shadow",
    "/etc/sudoers",
    "/etc/ssh/",
    "/root/.ssh",
    "/home/",
    "~/.ssh",
    "id_rsa",
    "id_ed25519",
    "id_dsa",
    ".pem",
    ".key",
    ".env",
    ".git-credentials",
    ".netrc",
    ".aws/",
    ".azure/",
    ".gcloud/",
    ".kube/",
    "credentials",
    "secrets",
    "ntuser.dat",
    "sam",
    "system",
    "windows/system32/config",
)


# ── 决策结果 ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SandboxDecision:
    """一次命令的路由决策。"""

    verdict: str          # "local" | "sandbox"
    tier: str | None      # "clean" | "net" | "work"（verdict==sandbox 时有值）
    lifecycle: str | None  # "persistent" | "ephemeral"
    reason: str           # 机器可读原因，用于审计 / 提示

    @property
    def is_sandbox(self) -> bool:
        return self.verdict == "sandbox"


def _lifecycle_for_cwd(cwd: str | None) -> str:
    """项目根在临时目录 → ephemeral（短期即删），否则 persistent。"""
    if not cwd:
        return "persistent"
    root = Path(cwd).resolve()
    temp_dirs = {
        Path(tempfile.gettempdir()).resolve(),
        Path("/tmp"),
        Path("/var/tmp"),
    }
    cache_dir = Path.home() / ".cache"
    for base in temp_dirs | {cache_dir.resolve()}:
        try:
            root.relative_to(base)
            return "ephemeral"
        except ValueError:
            continue
    return "persistent"


def _has_sensitive_path_arg(tokens: list[str], cwd: str | None) -> bool:
    """检查参数里是否出现敏感路径片段。"""
    base = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    lowered_tokens = [t.lower() for t in tokens[1:]]
    for token in lowered_tokens:
        # 去掉引号残留（shlex 已处理）与路径修饰符
        token = token.replace("\\", "/").rstrip("/")
        for frag in SENSITIVE_PATH_FRAGMENTS:
            if frag.lower() in token:
                return True
        # 工作区内的 .env / 密钥文件也属于敏感（相对路径命中）
        try:
            candidate = base / token
            if candidate.is_file():
                name = candidate.name.lower()
                if name in {".env", ".netrc", ".git-credentials"} or name.endswith(
                    (".pem", ".key")
                ):
                    return True
        except (OSError, ValueError):
            continue
    return False


def _first_token(command: str) -> str | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    return tokens[0].lower() if tokens else None


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return []


def _script_path_in_cwd(tokens: list[str], cwd: str | None) -> bool:
    """解释器命令的脚本参数是否落在当前工作区内。"""
    if not cwd:
        return False
    base = Path(cwd).resolve()
    args = tokens[1:]
    # 跳过解释器自身开关（python -u 等），找第一个非 flag 参数
    for arg in args:
        if arg in INLINE_FLAGS:
            return False
        if arg.startswith("-"):
            continue
        candidate = Path(arg)
        if not candidate.is_absolute():
            candidate = base / candidate
        try:
            candidate.resolve().relative_to(base)
            return True
        except (ValueError, OSError):
            return False
    return False


def classify(command: str, cwd: str | None = None) -> SandboxDecision:
    """对一条 shell 命令做沙箱路由决策。"""
    cwd = cwd or os.getcwd()
    lifecycle = _lifecycle_for_cwd(cwd)
    tokens = _tokens(command)
    if not tokens:
        return SandboxDecision("local", None, lifecycle, "empty_command")

    first = tokens[0].lower()
    base = first.split("\\")[-1].split("/")[-1]

    # 1. 脚本执行器：一律沙箱
    if base in SCRIPT_EXECUTORS:
        if base in SHELL_EXECUTORS:
            return SandboxDecision(
                "sandbox", "clean", lifecycle, "script_shell_executor"
            )
        if base in INTERPRETER_EXECUTORS:
            if _script_path_in_cwd(tokens, cwd):
                return SandboxDecision(
                    "sandbox", "work", lifecycle, "run_in_workspace"
                )
            return SandboxDecision(
                "sandbox", "clean", lifecycle, "script_inline_or_external"
            )
        return SandboxDecision("sandbox", "clean", lifecycle, "script_executor")

    # 2. 包安装器 → work
    if base in PACKAGE_INSTALLERS:
        sub = tokens[1].lower() if len(tokens) > 1 else ""
        if base in {"pip", "pip3", "uv", "poetry", "npm", "yarn", "pnpm", "cargo"}:
            if not sub or sub in INSTALL_SUBCOMMANDS:
                return SandboxDecision(
                    "sandbox", "work", lifecycle, "package_install"
                )
        if base in {"apt", "apt-get"} and sub in {"install", "update", "upgrade"}:
            return SandboxDecision("sandbox", "work", lifecycle, "package_install")

    # 3. 联网工具
    if base in NETWORK_TOOLS:
        return SandboxDecision("sandbox", "net", lifecycle, "network_tool")

    if base == "git":
        sub = tokens[1].lower() if len(tokens) > 1 else ""
        if sub in GIT_WRITE_SUBCOMMANDS:
            return SandboxDecision("sandbox", "work", lifecycle, "git_write")

    # 4. 敏感路径参数 → clean（即使命令在白名单里）
    if _has_sensitive_path_arg(tokens, cwd):
        return SandboxDecision("sandbox", "clean", lifecycle, "sensitive_path")

    # 5. 安全白名单 → local
    if is_safe_command(command):
        return SandboxDecision("local", None, lifecycle, "safe_whitelist")

    # 6. 其余风险命令 → clean（fail-safe）
    return SandboxDecision("sandbox", "clean", lifecycle, "unclassified_risky")


__all__ = [
    "SandboxDecision",
    "SCRIPT_EXECUTORS",
    "SHELL_EXECUTORS",
    "INTERPRETER_EXECUTORS",
    "PACKAGE_INSTALLERS",
    "NETWORK_TOOLS",
    "classify",
]
