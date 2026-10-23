# [Phase 7] 2026-08-06: Draft 工作副本 — 基于 git worktree 的安全实验环境。
"""Draft working copies (git worktree based).

用途：在正式项目上做有风险的修改 / 安装实验前，先创建一份工作副本：

- ``create``：在 git 仓库里 `git worktree add` 一个独立分支副本
  （放在 ~/.code-agent/drafts/<project_id>/<draft_id>，不污染工作区）；
- ``status``：查看副本的改动（git status --porcelain）；
- ``merge``：把副本改动以 squash 提交合并回主分支（主工作区必须干净）；
- ``discard``：删除副本 worktree + 分支 + 对应的沙箱 work 卷。

非 git 项目不支持（创建时给出明确错误）。Draft 与沙箱 work 卷独立：
副本的沙箱环境（按副本根路径哈希）与主项目环境互不干扰。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


DEFAULT_DRAFTS_DIR = Path.home() / ".code-agent" / "drafts"
DEFAULT_DRAFT_STATE = Path.home() / ".code-agent" / "sandbox" / "drafts.json"


class DraftError(RuntimeError):
    """Draft 操作的业务错误（可直接展示给用户）。"""


def _project_id(root: str) -> str:
    return hashlib.sha256(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:16]


def draft_volume_name(draft_root: str) -> str:
    return f"code-agent-{_project_id(draft_root)}-work"


@dataclass
class DraftRecord:
    draft_id: str
    project_id: str
    project_root: str
    root: str
    branch: str
    base_commit: str
    created_at: float
    merged: bool = False
    volume: str = field(default="")


class DraftManager:
    """创建与管理工作副本。"""

    def __init__(
        self,
        drafts_dir: str | Path | None = None,
        state_path: str | Path | None = None,
    ):
        self._drafts_dir = Path(drafts_dir or DEFAULT_DRAFTS_DIR)
        self._state_path = Path(state_path or DEFAULT_DRAFT_STATE)
        self._records: dict[str, DraftRecord] = {}
        self.load()

    # ── 持久化 ──────────────────────────────────────────────────────────────

    def load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for key, item in (raw.get("drafts") or {}).items():
            try:
                self._records[key] = DraftRecord(**item)
            except TypeError:
                continue

    def save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "drafts": {
                    did: asdict(rec)
                    for did, rec in sorted(self._records.items())
                }
            }
            tmp = self._state_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._state_path)
        except OSError:
            return

    # ── git 辅助 ────────────────────────────────────────────────────────────

    @staticmethod
    async def _git(cwd: str | Path, *args: str, check: bool = True) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
        stdout = out.decode("utf-8", errors="replace").strip()
        stderr = err.decode("utf-8", errors="replace").strip()
        if check and proc.returncode != 0:
            raise DraftError(stderr or f"git {args[0]} failed (exit {proc.returncode})")
        return stdout

    async def _ensure_git_repo(self, project_root: str) -> None:
        if not Path(project_root).is_dir():
            raise DraftError(f"项目目录不存在：{project_root}")
        try:
            await self._git(project_root, "rev-parse", "--is-inside-work-tree")
        except DraftError as exc:
            raise DraftError(
                f"Draft 需要 git 仓库：{project_root} 不是 git 项目（{exc}）"
            ) from exc

    # ── 操作 ────────────────────────────────────────────────────────────────

    async def create(self, project_root: str) -> DraftRecord:
        project_root = str(Path(project_root).resolve())
        await self._ensure_git_repo(project_root)

        dirty = await self._git(project_root, "status", "--porcelain")
        if dirty:
            raise DraftError(
                "主工作区有未提交改动；请先 commit/stash 再创建副本，"
                "否则副本基线不干净。"
            )

        project_id = _project_id(project_root)
        draft_id = uuid.uuid4().hex[:10]
        branch = f"code-agent-draft-{draft_id}"
        dest = self._drafts_dir / project_id / draft_id
        dest.parent.mkdir(parents=True, exist_ok=True)

        base_commit = await self._git(project_root, "rev-parse", "HEAD")
        try:
            await self._git(project_root, "worktree", "add", "-b", branch, str(dest))
        except DraftError as exc:
            raise DraftError(f"创建 worktree 失败：{exc}") from exc

        record = DraftRecord(
            draft_id=draft_id,
            project_id=project_id,
            project_root=project_root,
            root=str(dest),
            branch=branch,
            base_commit=base_commit,
            created_at=time.time(),
            volume=draft_volume_name(str(dest)),
        )
        self._records[draft_id] = record
        self.save()
        return record

    def get(self, draft_id: str) -> DraftRecord:
        record = self._records.get(draft_id)
        if record is None:
            raise DraftError(f"未找到 Draft：{draft_id}")
        return record

    def list_drafts(self) -> list[dict]:
        return [
            {
                "draft_id": rec.draft_id,
                "project_root": rec.project_root,
                "root": rec.root,
                "branch": rec.branch,
                "merged": rec.merged,
                "created_at": rec.created_at,
            }
            for rec in sorted(
                self._records.values(), key=lambda r: r.created_at, reverse=True
            )
        ]

    async def status(self, draft_id: str) -> dict:
        record = self.get(draft_id)
        if not Path(record.root).exists():
            raise DraftError(f"Draft 目录不存在：{record.root}")
        changed = await self._git(record.root, "status", "--porcelain")
        base_ok = record.base_commit == await self._git(
            record.root, "rev-parse", "HEAD"
        )
        return {
            "draft_id": record.draft_id,
            "root": record.root,
            "branch": record.branch,
            "base_commit": record.base_commit,
            "at_base": base_ok,
            "changed": changed or "(clean)",
        }

    async def merge(self, draft_id: str) -> str:
        record = self.get(draft_id)
        if not Path(record.root).exists():
            raise DraftError(f"Draft 目录不存在：{record.root}")

        # 主工作区必须干净
        dirty = await self._git(record.project_root, "status", "--porcelain")
        if dirty:
            raise DraftError(
                "主工作区有未提交改动；请先 commit/stash 再合并副本。"
            )

        # 先把副本改动提交到副本分支
        await self._git(record.root, "add", "-A")
        changed = await self._git(record.root, "status", "--porcelain")
        if changed:
            await self._git(
                record.root, "commit", "-m", f"code-agent draft {draft_id}",
            )
        else:
            return "no changes to merge"

        # squash 合并回主分支
        try:
            await self._git(record.project_root, "merge", "--squash", record.branch)
            await self._git(
                record.project_root, "commit", "-m", f"Apply draft {draft_id} (code-agent)",
            )
        except DraftError:
            await self._git(
                record.project_root, "merge", "--abort", check=False,
            )
            raise DraftError(
                "合并冲突，已中止；副本保留，可先解决主分支差异再重试。"
            ) from None

        record.merged = True
        self.save()
        return f"merged {record.draft_id} -> {record.project_root}"

    async def discard(self, draft_id: str) -> str:
        record = self.get(draft_id)
        await self._git(
            record.project_root, "worktree", "remove", "--force", record.root,
            check=False,
        )
        await self._git(
            record.project_root, "branch", "-D", record.branch, check=False,
        )
        await self._remove_draft_volume(record)
        self._records.pop(draft_id, None)
        self.save()
        return f"discarded {draft_id}"

    async def _remove_draft_volume(self, record: DraftRecord) -> None:
        if not record.volume:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "volume", "rm", "-f", record.volume,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=30)
        except (OSError, asyncio.TimeoutError):
            logger.warning("Failed to remove draft volume %s", record.volume)


__all__ = [
    "DEFAULT_DRAFTS_DIR",
    "DEFAULT_DRAFT_STATE",
    "DraftError",
    "DraftManager",
    "DraftRecord",
    "draft_volume_name",
]
