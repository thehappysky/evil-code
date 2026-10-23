# [Phase 5 / Phase 4] 2026-08-04: Tests for builtin/file_glob.py
"""Tests for the file_glob tool (Python fallback + formatting paths)."""

from __future__ import annotations

import pytest

from code_agent.tools.builtin.file_glob import file_glob, GlobInput
from code_agent.tools.result import ToolResult


@pytest.fixture
def tree(tmp_path):
    """Create a small tree for glob matching."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x", encoding="utf-8")
    (tmp_path / "src" / "util.py").write_text("y", encoding="utf-8")
    (tmp_path / "src" / "util.ts").write_text("z", encoding="utf-8")
    (tmp_path / "README.md").write_text("r", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("git", encoding="utf-8")
    return tmp_path


class TestFileGlobPythonFallback:
    async def test_glob_py_files(self, tree, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _name: None)  # force Python
        result = await file_glob(GlobInput(pattern="**/*.py", path=str(tree)))
        assert isinstance(result, ToolResult)
        assert not result.is_error
        lines = result.output.splitlines()
        rel = {ln for ln in lines if not ln.startswith("[")}
        assert "src/main.py" in rel
        assert "src/util.py" in rel
        assert "src/util.ts" not in rel

    async def test_glob_top_level_md(self, tree, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _name: None)
        result = await file_glob(GlobInput(pattern="*.md", path=str(tree)))
        assert "README.md" in result.output.splitlines()
        assert not result.is_error

    async def test_glob_skips_git(self, tree, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _name: None)
        result = await file_glob(GlobInput(pattern="**/*", path=str(tree)))
        assert "config" not in result.output  # .git internals excluded
        assert not result.is_error

    async def test_no_match(self, tree, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _name: None)
        result = await file_glob(GlobInput(pattern="**/*.rs", path=str(tree)))
        assert not result.is_error
        assert "No files found" in result.output

    async def test_missing_path_errors(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _name: None)
        result = await file_glob(GlobInput(pattern="*.py", path=str(tmp_path / "nope")))
        assert result.is_error

    async def test_truncation_footer(self, tree, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _name: None)
        result = await file_glob(GlobInput(pattern="**/*", path=str(tree)))
        assert not result.is_error
        assert "truncated" not in result.output.lower()  # few files, no cap hit


class TestFileGlobFormatting:
    async def test_mtime_sort_order(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _name: None)
        old = tmp_path / "old.txt"
        new = tmp_path / "new.txt"
        old.write_text("a", encoding="utf-8")
        new.write_text("b", encoding="utf-8")
        # Ensure distinct mtimes (touch order is not guaranteed on all FS).
        import os
        import time

        now = time.time()
        os.utime(old, (now - 100, now - 100))

        result = await file_glob(GlobInput(pattern="*.txt", path=str(tmp_path)))
        lines = result.output.splitlines()
        assert lines[0] == "new.txt"  # newest first
