# 2026-08-06: Tests for builtin/file_delete.py
"""Tests for the file_delete tool (host-side deletion via approval path)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from code_agent.tools.builtin.file_delete import DeleteFileInput, file_delete
from code_agent.tools.result import ToolResult


class TestFileDeleteFile:
    async def test_delete_normal_file(self, tmp_workspace: Path):
        """Delete a regular file."""
        target = tmp_workspace / "script.sh"
        target.write_text("echo hi", encoding="utf-8")

        result = await file_delete(DeleteFileInput(file_path=str(target)))

        assert isinstance(result, ToolResult)
        assert not result.is_error
        assert "Deleted file" in result.output
        assert not target.exists()

    async def test_delete_already_deleted_returns_error(self, tmp_workspace: Path):
        """Deleting a non-existent path is an error."""
        result = await file_delete(
            DeleteFileInput(file_path=str(tmp_workspace / "nope.py"))
        )
        assert result.is_error
        assert "does not exist" in result.output

    async def test_delete_relative_path_resolved(self, tmp_workspace: Path,
                                                  monkeypatch):
        """Relative paths resolve against cwd."""
        target = tmp_workspace / "rel.txt"
        target.write_text("x", encoding="utf-8")
        monkeypatch.chdir(tmp_workspace)

        result = await file_delete(DeleteFileInput(file_path="rel.txt"))

        assert not result.is_error
        assert not target.exists()


class TestFileDeleteDirectory:
    async def test_delete_empty_dir(self, tmp_workspace: Path):
        d = tmp_workspace / "empty"
        d.mkdir()

        result = await file_delete(DeleteFileInput(file_path=str(d)))

        assert not result.is_error
        assert "Deleted directory" in result.output
        assert not d.exists()

    async def test_delete_nonempty_dir(self, tmp_workspace: Path):
        """rmtree removes a directory with contents."""
        d = tmp_workspace / "pkg"
        d.mkdir()
        (d / "a.py").write_text("a", encoding="utf-8")
        (d / "sub").mkdir()
        (d / "sub" / "b.py").write_text("b", encoding="utf-8")

        result = await file_delete(DeleteFileInput(file_path=str(d)))

        assert not result.is_error
        assert not d.exists()

    async def test_delete_symlink_unlinks_not_rmtree(self, tmp_workspace: Path):
        """A symlink to a directory is unlinked, the target survives."""
        real = tmp_workspace / "real_dir"
        real.mkdir()
        (real / "keep.py").write_text("k", encoding="utf-8")
        link = tmp_workspace / "link"
        try:
            link.symlink_to(real, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks require admin/developer mode on this OS")

        result = await file_delete(DeleteFileInput(file_path=str(link)))

        assert not result.is_error
        assert "Deleted file" in result.output  # symlink → unlink path
        assert not link.exists()
        assert real.exists()  # target untouched
        assert (real / "keep.py").exists()

    async def test_delete_broken_symlink(self, tmp_workspace: Path):
        """A dangling symlink (target gone) is still removable."""
        link = tmp_workspace / "dangling"
        try:
            link.symlink_to(tmp_workspace / "gone")
        except OSError:
            pytest.skip("symlinks require admin/developer mode on this OS")

        result = await file_delete(DeleteFileInput(file_path=str(link)))

        assert not result.is_error
        assert not link.exists()


class TestFileDeleteSafety:
    async def test_refuse_workspace_root(self, tmp_workspace: Path,
                                          monkeypatch):
        """Cannot delete the cwd (workspace root)."""
        monkeypatch.chdir(tmp_workspace)

        result = await file_delete(DeleteFileInput(file_path="."))

        assert result.is_error
        assert "Refused" in result.output
        assert "workspace root" in result.output

    async def test_refuse_drive_root(self):
        """Cannot delete a filesystem root (path == path.parent)."""
        root = Path("C:\\") if sys.platform == "win32" else Path("/")

        result = await file_delete(DeleteFileInput(file_path=str(root)))

        assert result.is_error
        assert "Refused" in result.output

    async def test_refuse_system_path(self, tmp_path: Path, monkeypatch):
        """Cannot delete protected credential paths (~/.ssh)."""
        home = tmp_path / "fakehome"
        home.mkdir()
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "id_rsa").write_text("key", encoding="utf-8")

        monkeypatch.setattr(Path, "home", lambda: home)

        result = await file_delete(DeleteFileInput(file_path=str(ssh_dir)))

        assert result.is_error
        assert "Refused" in result.output
        assert ssh_dir.exists()  # not deleted


class TestFileDeletePermissionIntegration:
    """file_delete must NOT be pre-approved — it should reach the ASK/DENY
    gate like file_write. These assert the wiring, not runtime approval UX."""

    def test_not_in_read_tool_preapproval(self):
        from code_agent.permission.manager import _READ_TOOL_NAMES

        assert "file_delete" not in _READ_TOOL_NAMES

    def test_not_in_control_tool_preapproval(self):
        from code_agent.permission.manager import _CONTROL_TOOL_NAMES

        assert "file_delete" not in _CONTROL_TOOL_NAMES

    def test_in_read_write_capability_set(self):
        """read-write / execute sub-agents may use file_delete."""
        from code_agent.subagents.capabilities import capability_tool_names

        assert "file_delete" in capability_tool_names("read-write")
        assert "file_delete" in capability_tool_names("execute")

    def test_not_in_readonly_capability_set(self):
        """read-only sub-agents must NOT get file_delete."""
        from code_agent.subagents.capabilities import capability_tool_names

        assert "file_delete" not in capability_tool_names("read-only")
