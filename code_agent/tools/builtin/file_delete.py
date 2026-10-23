"""file_delete tool - Delete files or directories on the host.

Goes through the same host-side approval path as file_write (ASK in
interactive mode, DENY in headless unless allow-rule/YOLO). This is the
intended way to delete workspace files — shell_exec rm gets auto-routed
into the read-only sandbox and silently fails (workspace mounted :ro).

Features:
- Safety check: reject deletion of system/credential directories
- Refuse to delete workspace root or drive root
- Supports both files and directories (rmtree for dirs)
- Handles broken symlinks (unlink, not rmtree)
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from code_agent.tools.result import ToolResult

# 严禁删除的系统/凭证路径（删除不可逆，比 file_write 更严格）
_FORBIDDEN_PREFIXES = (
    "/etc/",
    "/sys/",
    "/proc/",
    "/boot/",
    "/dev/",
    "~/.ssh/",
    "~/.gnupg/",
    "~/.aws/",
    "~/.config/",
)


class DeleteFileInput(BaseModel):
    """Input for file_delete tool."""

    file_path: str = Field(description="Path to the file or directory to delete")


async def file_delete(input: DeleteFileInput) -> ToolResult:
    """Delete a file or directory on the host.

    Args:
        input: DeleteFileInput with file_path.

    Returns:
        ToolResult with deletion confirmation.
    """
    raw = Path(input.file_path).expanduser()
    path = raw.resolve()  # 归一化用于安全检查（会跟随符号链接 → 保守）

    # 安全：拒绝系统/凭证路径（归一化分隔符，跨平台匹配）
    path_norm = str(path).replace("\\", "/").rstrip("/").lower()
    home_norm = str(Path.home()).replace("\\", "/").rstrip("/").lower()
    for prefix in _FORBIDDEN_PREFIXES:
        expanded = prefix.replace("~", home_norm).rstrip("/")
        if path_norm == expanded or path_norm.startswith(expanded + "/"):
            return ToolResult.error(
                f"Refused: cannot delete protected path ({prefix}). Path: {path}"
            )

    # 安全：拒绝删除 workspace 根 / 驱动器根（path == path.parent 即根目录）
    cwd = Path.cwd().resolve()
    if path == cwd or path == path.parent:
        return ToolResult.error(
            f"Refused: cannot delete workspace root or drive root. Path: {path}"
        )

    # 实际删除目标：符号链接用未解析路径（删链接本身，不删目标）；
    # 普通文件/目录用解析后的路径。.resolve() 会跟随符号链接，若直接用它删
    # symlink-to-dir 会 rmtree 掉目标目录。
    target = raw if raw.is_symlink() else path

    # 不存在（断链 is_symlink 仍 True，不会误报）
    if not target.exists() and not target.is_symlink():
        return ToolResult.error(f"Path does not exist: {path}")

    try:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
            return ToolResult.ok(f"Deleted directory: {path}")
        os.unlink(target)
        return ToolResult.ok(f"Deleted file: {path}")
    except OSError as exc:
        return ToolResult.error(f"Failed to delete {path}: {exc}")
