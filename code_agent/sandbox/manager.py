# [Phase 7] 2026-08-05: SandboxManager — 项目级沙箱生命周期与执行路由。
"""Project-bound sandbox manager.

设计要点（对应 design-docs/沙箱方案-v1.md）：

- 长期/短期两类环境：项目根在临时目录 → ephemeral（用完即删）；否则
  persistent（磁盘压力下按重视度驱逐）。
- clean/net 容器每次命令 ``docker run --rm``，无状态；
  work 容器挂载命名卷 ``code-agent-<pid>-work`` 到 /env，依赖跨命令保留。
- net/work 出站流量走 EgressProxy 白名单。
- 定时 GC：清理已退出容器、悬空 ephemeral 卷、超限项目与磁盘水位。
- Docker 不可用时：run() 在本机执行并标记 fallback（必须已经过显式审批）。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shlex
import shutil
import time
import uuid
from pathlib import Path

from code_agent.sandbox.policy import SandboxDecision, classify
from code_agent.sandbox.proxy import DEFAULT_EGRESS_ALLOWLIST, EgressProxy
from code_agent.sandbox.state import DEFAULT_STATE_PATH, SandboxStateStore
from code_agent.sandbox.config import load_sandbox_toml, resolve_egress_mode
from code_agent.tools.builtin.shell_exec import ShellInput, shell_exec
from code_agent.tools.result import ToolResult

logger = logging.getLogger(__name__)


SANDBOX_IMAGE = "python:3.12-slim"
MANAGED_LABEL = "code-agent.managed=1"
DEFAULT_MAX_PROJECTS = 10
DEFAULT_DISK_WARNING_BYTES = 10 * 1024**3
DEFAULT_DISK_TARGET_RATIO = 0.9
DEFAULT_PROXY_PORT = 0


def resolve_sandbox_mode(cli_flag: bool = False) -> str:
    """优先级：环境变量 CODE_AGENT_SANDBOX > --sandbox > 默认 auto。"""
    env = os.environ.get("CODE_AGENT_SANDBOX", "").strip().lower()
    if env in {"off", "auto", "strict"}:
        return env
    return "strict" if cli_flag else "auto"


def _project_id(root: str) -> str:
    return hashlib.sha256(os.path.abspath(root).encode("utf-8")).hexdigest()[:16]


def _docker_volume_name(project_id: str) -> str:
    return f"code-agent-{project_id}-work"


def _host_mount_path(path: str) -> str:
    return path.replace("\\", "/")


class SandboxManager:
    """项目级沙箱执行与生命周期管理。"""

    def __init__(
        self,
        mode: str = "auto",
        *,
        egress: str | None = None,
        cwd: str | None = None,
        state_path: str | os.PathLike | None = None,
        session_id: str | None = None,
        max_projects: int = DEFAULT_MAX_PROJECTS,
        disk_warning_bytes: int = DEFAULT_DISK_WARNING_BYTES,
        image: str | None = None,
        profile: str | None = None,
    ):
        if mode not in {"off", "auto", "strict"}:
            raise ValueError(f"unknown sandbox mode: {mode!r}")
        self._mode = mode
        self._session_id = session_id or uuid.uuid4().hex[:8]
        self._cwd = str(Path(cwd or os.getcwd()).resolve())
        self._egress = resolve_egress_mode(egress)
        toml = load_sandbox_toml(self._cwd)
        self._egress_extra = list(toml.egress_extra)
        self._image = image or toml.image or SANDBOX_IMAGE
        self._profile_name = (
            os.environ.get("CODE_AGENT_SANDBOX_PROFILE", "").strip()
            or profile
            or toml.profile
        )
        self._profile = toml.profiles.get(self._profile_name) if self._profile_name else None
        self._deny_dir = (
            Path.home() / ".code-agent" / "sandbox" / "deny" / self._session_id
        )
        self._state = SandboxStateStore(state_path)
        self._max_projects = (
            toml.max_projects if toml.max_projects is not None else max_projects
        )
        self._disk_warning_bytes = (
            int(toml.disk_warning_gb * 1024**3)
            if toml.disk_warning_gb is not None
            else disk_warning_bytes
        )
        self._proxy: EgressProxy | None = None
        self._lock = asyncio.Lock()
        self._available: bool | None = None
        self._available_at = 0.0
        self._image_pulled = False
        self._ephemeral_volumes: set[str] = set()
        self._net_container: str | None = None
        self._net_container_root: str | None = None
        self._docker_argv = ("docker",)

    # ── 基础信息 ─────────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def egress(self) -> str:
        return self._egress

    @property
    def state(self) -> SandboxStateStore:
        return self._state

    def status(self) -> dict:
        return {
            "mode": self._mode,
            "egress": self._egress,
            "cwd": self._cwd,
            "projects": len(self._state.records()),
            "max_projects": self._max_projects,
            "profile": self._profile_name or "default",
        }

    # ── Docker 可用性（缓存 30s） ────────────────────────────────────────────

    async def is_available(self) -> bool:
        if self._available is not None and time.time() - self._available_at < 30:
            return self._available
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._docker_argv, "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                code = await asyncio.wait_for(proc.wait(), timeout=15)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                code = 1
            self._available = code == 0
        except (OSError, FileNotFoundError):
            self._available = False
        self._available_at = time.time()
        return self._available

    async def _ensure_image(self) -> ToolResult | None:
        if self._image_pulled:
            return None
        proc = await asyncio.create_subprocess_exec(
            *self._docker_argv, "pull", self._image,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            code = await asyncio.wait_for(proc.wait(), timeout=600)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult.error(
                "Sandbox image pull timed out; Docker may be starting up."
            )
        if code != 0:
            return ToolResult.error(
                f"Failed to pull sandbox image {self._image} (exit {code})."
            )
        self._image_pulled = True
        return None

    # ── 执行入口 ─────────────────────────────────────────────────────────────

    async def run(
        self,
        command: str,
        workdir: str | None = None,
        timeout: int = 120,
        allow_fallback: bool = True,
    ) -> ToolResult:
        timeout = min(max(int(timeout), 1), 600)
        cwd = os.path.abspath(workdir or self._cwd)
        decision = classify(command, cwd=cwd)

        if self._mode == "off":
            return await self._run_local(command, cwd, timeout, decision)

        # strict 模式：即使是白名单命令也强制进沙箱（用 clean 兜底）
        if self._mode == "strict" and decision.verdict != "sandbox":
            decision = SandboxDecision(
                "sandbox", "clean", decision.lifecycle, "strict_force"
            )

        if decision.verdict == "local":
            return await self._run_local(command, cwd, timeout, decision)

        if not await self.is_available():
            if allow_fallback:
                # 权限层已经要求显式审批；这里在用户批准后本机执行并明确标记。
                logger.warning(
                    "Sandbox unavailable; running locally after approval: %s",
                    command[:120],
                )
                return await self._run_local(
                    command, cwd, timeout, decision, fallback=True
                )
            return ToolResult.error(
                "Sandbox is unavailable (Docker not running) and this context "
                "does not allow falling back to the host. "
                "Start Docker Desktop and retry, or disable the sandbox."
            )

        return await self._run_sandbox(command, cwd, timeout, decision)

    async def _run_local(
        self,
        command: str,
        cwd: str,
        timeout: int,
        decision: SandboxDecision,
        fallback: bool = False,
    ) -> ToolResult:
        result = await shell_exec(
            ShellInput(command=command, timeout=timeout, workdir=cwd)
        )
        result.meta = {
            "sandbox": False,
            "mode": "local",
            "fallback": fallback,
            "reason": decision.reason,
        }
        return result

    # ── 沙箱执行 ─────────────────────────────────────────────────────────────

    async def _run_sandbox(
        self,
        command: str,
        cwd: str,
        timeout: int,
        decision: SandboxDecision,
    ) -> ToolResult:
        tier = decision.tier or "clean"
        lifecycle = decision.lifecycle or "persistent"
        project_id = _project_id(cwd)
        root = _host_mount_path(os.path.abspath(cwd))

        self._state.record_use(project_id, cwd, lifecycle)

        err = await self._ensure_image()
        if err is not None:
            return err

        if tier in {"net", "work"} and self._egress == "allowlist":
            await self._ensure_proxy()

        if tier == "work":
            await self._ensure_work_volume(project_id, lifecycle)

        # net 档复用会话级容器（docker exec），避免每次 docker run 的开销
        if (
            tier == "net"
            and not self._profile_restricts_network()
            and self._profile is None
        ):
            if await self._ensure_net_container(cwd):
                return await self._run_in_net_container(
                    command, cwd, timeout, tier, lifecycle, project_id,
                    decision.reason,
                )
            # 容器创建失败（或工作目录变化）→ 回退到每次 docker run

        argv = self._build_docker_argv(
            command, root, project_id, tier, lifecycle
        )
        logger.debug("sandbox run: %s", " ".join(argv[:16]) + " ...")

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return self._sandbox_result(
                    ToolResult.error(
                        f"Sandbox command timed out after {timeout}s and was killed.\n"
                        f"Command: {command[:200]}"
                    ),
                    tier,
                    lifecycle,
                    project_id,
                    decision.reason,
                )
        except FileNotFoundError:
            return ToolResult.error(
                "Docker is not installed. Install Docker to use sandboxed execution."
            )
        except OSError as exc:
            return ToolResult.error(f"Sandbox execution error: {exc}")

        stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
        stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""
        parts = []
        if stdout_text:
            parts.append(stdout_text)
        if stderr_text:
            parts.append(f"[stderr]\n{stderr_text}")
        output = "\n".join(parts) if parts else "(no output)"
        if proc.returncode != 0:
            output += f"\n\n[Exit code: {proc.returncode}]"
            result = ToolResult.error(output)
        else:
            result = ToolResult.ok(output)

        # ephemeral work 卷用完即删
        if lifecycle == "ephemeral" and tier == "work":
            self._ephemeral_volumes.add(_docker_volume_name(project_id))
            await self._remove_volume(_docker_volume_name(project_id))

        return self._sandbox_result(
            result, tier, lifecycle, project_id, decision.reason
        )

    def _profile_restricts_network(self) -> bool:
        if self._profile is None or self._profile.restrict_network is None:
            return False
        return bool(self._profile.restrict_network)

    async def _run_in_net_container(
        self,
        command: str,
        cwd: str,
        timeout: int,
        tier: str,
        lifecycle: str,
        project_id: str,
        reason: str,
    ) -> ToolResult:
        inner = f"timeout --signal=KILL {timeout}s bash -c {shlex.quote(command)}"
        argv = [
            *self._docker_argv, "exec",
            self._net_container or "",
            "bash", "-c", inner,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout + 10
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return self._sandbox_result(
                    ToolResult.error(
                        f"Sandbox command timed out after {timeout}s and was killed.\n"
                        f"Command: {command[:200]}"
                    ),
                    tier, lifecycle, project_id, reason,
                )
        except (OSError, FileNotFoundError) as exc:
            return ToolResult.error(f"Sandbox execution error: {exc}")

        stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
        stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""
        parts = []
        if stdout_text:
            parts.append(stdout_text)
        if stderr_text:
            parts.append(f"[stderr]\n{stderr_text}")
        output = "\n".join(parts) if parts else "(no output)"
        if proc.returncode != 0:
            output += f"\n\n[Exit code: {proc.returncode}]"
            result = ToolResult.error(output)
        else:
            result = ToolResult.ok(output)
        return self._sandbox_result(
            result, tier, lifecycle, project_id, reason,
        )

    async def _ensure_net_container(self, cwd: str) -> bool:
        root = _host_mount_path(os.path.abspath(cwd))
        if self._net_container is not None:
            if self._net_container_root != root:
                return False  # 工作目录变化：交给 docker run 路径
            return True
        name = f"code-agent-net-{self._session_id}"
        argv = [
            *self._docker_argv,
            "run", "-d", "--name", name,
            "--label", "code-agent.managed=1",
            "--label", "code-agent.tier=net",
            "--network", "bridge",
            "--memory", "512m",
            "--cpus", "1",
            "--user", "0:0",
            "--mount", "type=tmpfs,target=/scratch",
            "--add-host", "host.docker.internal:host-gateway",
            "-v", f"{root}:/workspace:ro",
            "-w", "/workspace",
        ]
        if self._egress == "allowlist" and self._proxy is not None:
            proxy_url = self._proxy.proxy_url
            argv += [
                "-e", f"HTTP_PROXY={proxy_url}",
                "-e", f"HTTPS_PROXY={proxy_url}",
                "-e", f"ALL_PROXY={proxy_url}",
                "-e", "NO_PROXY=localhost,127.0.0.1,host.docker.internal",
                "-e", "TMPDIR=/scratch",
            ]
        argv += [self._image, "bash", "-c", "sleep infinity"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=60)
        except (OSError, asyncio.TimeoutError):
            return False
        if proc.returncode != 0:
            return False
        self._net_container = name
        self._net_container_root = root
        return True

    @staticmethod
    def _sandbox_result(
        result: ToolResult,
        tier: str,
        lifecycle: str,
        project_id: str,
        reason: str,
    ) -> ToolResult:
        result.meta = {
            "sandbox": True,
            "mode": "sandbox",
            "tier": tier,
            "lifecycle": lifecycle,
            "project_id": project_id,
            "reason": reason,
        }
        return result

    def _build_docker_argv(
        self,
        command: str,
        root: str,
        project_id: str,
        tier: str,
        lifecycle: str,
    ) -> list[str]:
        network = "none" if tier == "clean" else "bridge"
        if self._profile_restricts_network():
            network = "none"
        ro = "ro" if tier != "work" else "ro"

        argv: list[str] = [
            *self._docker_argv,
            "run", "--rm",
            "--label", "code-agent.managed=1",
            "--label", f"code-agent.project={project_id}",
            "--label", f"code-agent.tier={tier}",
            "--label", f"code-agent.lifecycle={lifecycle}",
            "--network", network,
            "--memory", "512m",
            "--cpus", "1",
            "--stop-timeout", "10",
            "--user", "0:0",
            "--mount", "type=tmpfs,target=/scratch",
            "-e", "TMPDIR=/scratch",
            "-v", f"{root}:/workspace:{ro}",
            "-w", "/workspace",
        ]

        # 自定义 profile：额外只读/可写挂载 + deny 路径占位绑定
        if self._profile is not None:
            for index, path in enumerate(self._profile.read_only):
                argv += [
                    "-v", f"{_host_mount_path(path)}:/mnt/ro-{index}:ro",
                ]
            for index, path in enumerate(self._profile.read_write):
                argv += [
                    "-v", f"{_host_mount_path(path)}:/mnt/rw-{index}:rw",
                ]
            argv += self._deny_placeholder_args(root)

        if tier in {"net", "work"}:
            argv += ["--add-host", "host.docker.internal:host-gateway"]
            if self._egress == "allowlist" and self._proxy is not None:
                proxy_url = self._proxy.proxy_url
                argv += [
                    "-e", f"HTTP_PROXY={proxy_url}",
                    "-e", f"HTTPS_PROXY={proxy_url}",
                    "-e", f"ALL_PROXY={proxy_url}",
                    "-e", "NO_PROXY=localhost,127.0.0.1,host.docker.internal",
                    "-e", "GIT_CONFIG_COUNT=2",
                    "-e", f"GIT_CONFIG_KEY_0=http.proxy",
                    "-e", f"GIT_CONFIG_VALUE_0={proxy_url}",
                    "-e", "GIT_CONFIG_KEY_1=https.proxy",
                    "-e", f"GIT_CONFIG_VALUE_1={proxy_url}",
                ]

        if tier == "work":
            volume = _docker_volume_name(project_id)
            argv += [
                "-v", f"{volume}:/env",
                "-e", "HOME=/env/home",
                "-e", "PIP_CACHE_DIR=/env/.cache/pip",
                "-e", "PYTHONUSERBASE=/env/.local",
                "-e", "PYTHONPATH=/env/.local/lib/python3.12/site-packages",
                "-e", "npm_config_cache=/env/.npm",
                "-e", "npm_config_prefix=/env/.npm-global",
                "-e",
                "PATH=/env/.local/bin:/env/.npm-global/bin:/usr/local/bin:/usr/bin:/bin",
            ]

        argv += [self._image, "bash", "-c", command]
        return argv

    async def _ensure_proxy(self) -> None:
        if self._proxy is not None:
            return
        allowlist = DEFAULT_EGRESS_ALLOWLIST | set(self._egress_extra)
        # 容器经 host.docker.internal 访问宿主，代理需监听所有接口
        self._proxy = EgressProxy(allowlist=allowlist, host="0.0.0.0")
        await self._proxy.start()

    async def _ensure_work_volume(self, project_id: str, lifecycle: str) -> None:
        """显式创建带标签的 work 卷，便于 GC 定向清理。"""
        name = _docker_volume_name(project_id)
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._docker_argv, "volume", "create",
                "--label", "code-agent.managed=1",
                "--label", f"code-agent.project={project_id}",
                "--label", f"code-agent.lifecycle={lifecycle}",
                name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=30)
        except (OSError, asyncio.TimeoutError):
            logger.warning("Failed to create sandbox volume %s", name)

    async def _remove_volume(self, name: str) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._docker_argv, "volume", "rm", "-f", name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=30)
        except (OSError, asyncio.TimeoutError):
            logger.warning("Failed to remove sandbox volume %s", name)

    def _deny_placeholder_args(self, root_host: str) -> list[str]:
        """deny 路径用空占位文件/目录绑定覆盖（容器内不可见/不可读）。"""
        args: list[str] = []
        if self._profile is None or not self._profile.deny:
            return args
        base = Path(root_host).resolve()
        for entry in self._profile.deny:
            candidate = Path(entry)
            candidate = candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()
            try:
                rel = candidate.relative_to(base)
            except ValueError:
                continue  # 工作区外路径不挂载，容器天然不可见
            if not candidate.exists():
                continue
            placeholder = self._deny_dir / rel.as_posix()
            if candidate.is_dir():
                placeholder.mkdir(parents=True, exist_ok=True)
            else:
                placeholder.parent.mkdir(parents=True, exist_ok=True)
                if placeholder.exists():
                    if placeholder.is_dir():
                        shutil.rmtree(placeholder, ignore_errors=True)
                    else:
                        placeholder.unlink(missing_ok=True)
                placeholder.write_bytes(b"")
            args += [
                "-v",
                f"{_host_mount_path(str(placeholder))}:/workspace/{rel.as_posix()}:ro",
            ]
        return args

    # ── 生命周期：GC / 驱逐 ──────────────────────────────────────────────────

    async def gc(self, force: bool = False) -> dict:
        """定时/按需清理：退出容器、悬空 ephemeral 卷、项目上限、磁盘水位。"""
        stats = {
            "containers_removed": 0,
            "volumes_removed": 0,
            "projects_evicted": 0,
            "disk_bytes": None,
        }
        if not await self.is_available():
            return stats

        # 1. 已退出的受管容器
        ids = await self._docker_output(
            "ps", "-a",
            "--filter", "label=code-agent.managed=1",
            "--filter", "status=exited",
            "--format", "{{.ID}}",
        )
        for cid in (line.strip() for line in ids.splitlines() if line.strip()):
            await self._docker_exec("rm", cid)
            stats["containers_removed"] += 1

        # 2. 悬空的 ephemeral 卷
        volumes = await self._docker_output(
            "volume", "ls", "-q",
            "--filter", "dangling=true",
            "--filter", "label=code-agent.managed=1",
            "--filter", "label=code-agent.lifecycle=ephemeral",
        )
        for name in (line.strip() for line in volumes.splitlines() if line.strip()):
            await self._remove_volume(name)
            stats["volumes_removed"] += 1

        # 3. 项目数上限（LRU / 重视度驱逐，保护项目除外）
        victims = self._evict_until_project_limit()
        for victim_id in victims:
            await self._remove_volume(_docker_volume_name(victim_id))
            self._state.remove(victim_id)
        stats["projects_evicted"] += len(victims)

        # 4. 磁盘水位
        disk_bytes = await self._docker_disk_usage()
        stats["disk_bytes"] = disk_bytes
        if disk_bytes is not None and disk_bytes > self._disk_warning_bytes:
            target = int(self._disk_warning_bytes * DEFAULT_DISK_TARGET_RATIO)
            sizes = await self._volume_sizes()
            victims = self._evict_persistent_until(
                target_bytes=target,
                current_bytes=disk_bytes,
                sizes=sizes,
            )
            for victim_id in victims:
                await self._remove_volume(_docker_volume_name(victim_id))
                self._state.remove(victim_id)
            stats["projects_evicted"] += len(victims)

        self._state.save()
        return stats

    def _evict_until_project_limit(self) -> list[str]:
        victims: list[str] = []
        while True:
            records = [
                r for r in self._state.records()
                if not r.protected and r.lifecycle == "persistent"
            ]
            if len(records) <= self._max_projects:
                break
            victim = min(records, key=lambda r: (r.importance(), r.last_used))
            victims.append(victim.project_id)
            self._state.remove(victim.project_id)
        return victims

    def _evict_persistent_until(
        self,
        target_bytes: int,
        current_bytes: int,
        sizes: dict[str, int] | None = None,
    ) -> list[str]:
        """水位超限时按重视度驱逐 persistent 项目卷，直到低于目标或无可驱逐。"""
        victims: list[str] = []
        while current_bytes > target_bytes:
            candidates = [
                r for r in self._state.records()
                if not r.protected and r.lifecycle == "persistent"
            ]
            if not candidates:
                break
            victim = min(candidates, key=lambda r: (r.importance(), r.last_used))
            victims.append(victim.project_id)
            self._state.remove(victim.project_id)
            volume = _docker_volume_name(victim.project_id)
            freed = (sizes or {}).get(volume) or 256 * 1024 * 1024
            current_bytes -= freed
        return victims

    async def _volume_sizes(self) -> dict[str, int]:
        """用 docker system df -v 读取受管卷的真实大小（失败则回退估算）。"""
        out = await self._docker_output(
            "system", "df", "-v",
            "--format", "{{.Type}}\t{{.Name}}\t{{.Size}}",
        )
        sizes: dict[str, int] = {}
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and parts[0].strip() == "Volume":
                name = parts[1].strip()
                if name.startswith("code-agent-") and name.endswith("-work"):
                    sizes[name] = _parse_size(parts[2])
        return sizes

    async def _docker_output(self, *args: str) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._docker_argv, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            return out.decode("utf-8", errors="replace")
        except (OSError, asyncio.TimeoutError):
            return ""

    async def _docker_exec(self, *args: str) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._docker_argv, *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=60)
        except (OSError, asyncio.TimeoutError):
            pass

    async def _docker_disk_usage(self) -> int | None:
        """用 docker system df 统计镜像/容器/卷/缓存总占用（字节）。"""
        out = await self._docker_output(
            "system", "df", "--format", "{{.Type}} {{.Size}}"
        )
        total = 0
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2:
                total += _parse_size(parts[1])
        if not out.strip():
            return None
        return total

    # ── 手动管理命令 ─────────────────────────────────────────────────────────

    def list_projects(self) -> list[dict]:
        return [
            {
                "project_id": r.project_id,
                "root": r.root,
                "lifecycle": r.lifecycle,
                "importance": r.importance(),
                "last_used": r.last_used,
                "protected": r.protected,
                "volume": _docker_volume_name(r.project_id) if r.lifecycle == "persistent" else None,
            }
            for r in sorted(
                self._state.records(),
                key=lambda r: (-r.importance(), -r.last_used),
            )
        ]

    def protect_project(self, project_id: str) -> bool:
        return self._state.protect(project_id)

    def unprotect_project(self, project_id: str) -> bool:
        return self._state.unprotect(project_id)

    def remove_project(self, project_id: str) -> bool:
        return self._state.remove(project_id)

    async def clean_cache(self) -> dict:
        """手动清理：dangling 镜像 + 已退出容器 + 悬空受管卷。"""
        stats = {"images_reclaimed": 0}
        if not await self.is_available():
            return stats
        out = await self._docker_output(
            "image", "prune", "-f", "--filter", "dangling=true",
        )
        m = re.search(r"Total reclaimed space:\s*(.+)", out)
        if m:
            stats["reclaimed"] = m.group(1).strip()
        gc = await self.gc()
        stats.update(gc)
        return stats

    async def close(self) -> None:
        """会话结束：停代理、删本次会话的 ephemeral 卷。"""
        if self._net_container is not None:
            await self._docker_exec("rm", "-f", self._net_container)
            self._net_container = None
        if self._proxy is not None:
            await self._proxy.stop()
            self._proxy = None
        for name in list(self._ephemeral_volumes):
            await self._remove_volume(name)
        self._ephemeral_volumes.clear()
        shutil.rmtree(self._deny_dir, ignore_errors=True)
        self._state.save()


def _parse_size(text: str) -> int:
    """解析 docker system df 的 Size 列（如 1.2GB / 512MB / 10B）。"""
    text = text.strip()
    m = re.match(r"^([\d.]+)\s*([kKmMgGtT]?i?B)?$", text)
    if not m:
        return 0
    value = float(m.group(1))
    unit = (m.group(2) or "B").upper()
    multipliers = {
        "B": 1,
        "KB": 1024,
        "KIB": 1024,
        "MB": 1024**2,
        "MIB": 1024**2,
        "GB": 1024**3,
        "GIB": 1024**3,
        "TB": 1024**4,
        "TIB": 1024**4,
    }
    return int(value * multipliers.get(unit, 1))


__all__ = [
    "DEFAULT_MAX_PROJECTS",
    "DEFAULT_DISK_WARNING_BYTES",
    "MANAGED_LABEL",
    "SANDBOX_IMAGE",
    "SandboxManager",
    "resolve_sandbox_mode",
]
