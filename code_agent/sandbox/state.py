# [Phase 7] 2026-08-05: 沙箱项目状态存储 — 重视度 / 保护标记 / 生命周期。
"""Sandbox project state persistence.

每个沙箱环境绑定一个 Project ID（项目根路径哈希）。这里记录：

- last_used：最近一次使用时间（新鲜度）
- access_count：最近一个时间窗内的使用次数（频率）
- protected：用户手动保护，永不自动驱逐

重视度 = 时间窗内访问频率，平手时最近使用的新鲜度优先。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


DEFAULT_STATE_PATH = Path.home() / ".code-agent" / "sandbox" / "state.json"
IMPORTANCE_WINDOW_SECONDS = 7 * 24 * 3600  # 7 天


@dataclass
class ProjectRecord:
    project_id: str
    root: str
    last_used: float = 0.0
    access_count: int = 0
    window_start: float = field(default_factory=time.time)
    protected: bool = False
    lifecycle: str = "persistent"

    def touch(self, now: float | None = None) -> None:
        now = now or time.time()
        if now - self.window_start > IMPORTANCE_WINDOW_SECONDS:
            self.window_start = now
            self.access_count = 0
        self.access_count += 1
        self.last_used = now

    def importance(self, now: float | None = None) -> float:
        """重视度分数：窗口内访问次数；过期窗口视为 0。"""
        now = now or time.time()
        if now - self.window_start > IMPORTANCE_WINDOW_SECONDS:
            return 0.0
        return float(self.access_count)


class SandboxStateStore:
    """JSON 文件存储的项目状态（线程安全由调用方保证）。"""

    def __init__(self, path: str | os.PathLike | None = None):
        self._path = Path(path or DEFAULT_STATE_PATH)
        self._records: dict[str, ProjectRecord] = {}
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for key, item in (raw.get("projects") or {}).items():
            try:
                self._records[key] = ProjectRecord(**item)
            except TypeError:
                continue

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "projects": {
                    pid: asdict(rec)
                    for pid, rec in sorted(self._records.items())
                }
            }
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except OSError:
            return

    def get_or_create(self, project_id: str, root: str, lifecycle: str) -> ProjectRecord:
        rec = self._records.get(project_id)
        if rec is None:
            rec = ProjectRecord(
                project_id=project_id,
                root=root,
                lifecycle=lifecycle,
            )
            self._records[project_id] = rec
        elif rec.lifecycle != lifecycle:
            rec.lifecycle = lifecycle
        return rec

    def record_use(self, project_id: str, root: str, lifecycle: str) -> ProjectRecord:
        rec = self.get_or_create(project_id, root, lifecycle)
        rec.touch()
        self.save()
        return rec

    def protect(self, project_id: str) -> bool:
        rec = self._records.get(project_id)
        if rec is None:
            return False
        rec.protected = True
        self.save()
        return True

    def unprotect(self, project_id: str) -> bool:
        rec = self._records.get(project_id)
        if rec is None:
            return False
        rec.protected = False
        self.save()
        return True

    def remove(self, project_id: str) -> bool:
        removed = self._records.pop(project_id, None) is not None
        if removed:
            self.save()
        return removed

    def records(self) -> list[ProjectRecord]:
        return list(self._records.values())

    def eviction_order(self) -> list[ProjectRecord]:
        """按重视度从低到高排序（低者先驱逐）。"""
        now = time.time()
        return sorted(
            self._records.values(),
            key=lambda r: (r.importance(now), r.last_used),
        )


__all__ = [
    "DEFAULT_STATE_PATH",
    "IMPORTANCE_WINDOW_SECONDS",
    "ProjectRecord",
    "SandboxStateStore",
]
