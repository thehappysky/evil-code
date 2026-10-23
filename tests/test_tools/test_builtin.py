"""Tests for built-in tools: file_read, file_edit, file_write, shell_exec, file_search.

Covers:
- file_read: normal / binary detection / offset+limit / missing file
- file_edit: unique match / multiple matches / not found / atomic write
- file_write: create new / overwrite / forbidden dir
- shell_exec: success / timeout / error exit code
- file_search: pattern match / no match / case insensitive

Note: web_fetch is excluded from unit tests (requires network mocking
beyond pytest-httpx scope for DNS/redirect logic).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_agent.tools.builtin.file_read import ReadFileInput, file_read
from code_agent.tools.builtin.file_edit import SearchReplaceInput, file_edit
from code_agent.tools.builtin.file_write import WriteFileInput, file_write
from code_agent.tools.builtin.shell_exec import ShellInput, shell_exec
from code_agent.tools.builtin.file_search import SearchInput, file_search


# ─── file_read ─────────────────────────────────────────────────────────────────


class TestFileRead:
    async def test_read_normal_file(self, tmp_workspace: Path):
        """Read a normal text file with line numbers."""
        test_file = tmp_workspace / "hello.py"
        test_file.write_text("line1\nline2\nline3\n", encoding="utf-8")

        result = await file_read(ReadFileInput(file_path=str(test_file)))

        assert result.is_error is False
        assert "line1" in result.output
        assert "line2" in result.output
        # Line numbers present
        assert "1|" in result.output or "     1|" in result.output

    async def test_read_missing_file(self, tmp_workspace: Path):
        """Reading a non-existent file returns error."""
        result = await file_read(
            ReadFileInput(file_path=str(tmp_workspace / "nope.txt"))
        )
        assert result.is_error is True

    async def test_read_binary_file(self, tmp_workspace: Path):
        """Binary file (high null byte ratio) is detected and rejected."""
        bin_file = tmp_workspace / "data.bin"
        # >10% null bytes in first 8KB
        bin_file.write_bytes(b"\x00" * 1000 + b"\x41" * 100)

        result = await file_read(ReadFileInput(file_path=str(bin_file)))
        assert result.is_error is True
        assert "binary" in result.output.lower()

    async def test_read_with_offset_limit(self, tmp_workspace: Path):
        """offset and limit control which lines are returned."""
        test_file = tmp_workspace / "lines.txt"
        lines = [f"line_{i}" for i in range(100)]
        test_file.write_text("\n".join(lines), encoding="utf-8")

        result = await file_read(
            ReadFileInput(file_path=str(test_file), offset=10, limit=5)
        )

        assert result.is_error is False
        assert "line_10" in result.output
        assert "line_14" in result.output
        # Should NOT contain line_15 (offset=10, limit=5 -> lines 10-14)
        assert "line_15" not in result.output

    async def test_read_empty_file(self, tmp_workspace: Path):
        """Empty file returns appropriate message."""
        empty = tmp_workspace / "empty.txt"
        empty.write_text("", encoding="utf-8")

        result = await file_read(ReadFileInput(file_path=str(empty)))
        assert result.is_error is False


# ─── file_edit ─────────────────────────────────────────────────────────────────


class TestFileEdit:
    async def test_unique_match_replace(self, tmp_workspace: Path):
        """Successful edit with unique old_string match."""
        target = tmp_workspace / "code.py"
        target.write_text("def foo():\n    return 1\n", encoding="utf-8")

        result = await file_edit(
            SearchReplaceInput(
                file_path=str(target),
                old_string="return 1",
                new_string="return 42",
            )
        )

        assert result.is_error is False
        assert "return 42" in target.read_text(encoding="utf-8")

    async def test_multiple_matches_error(self, tmp_workspace: Path):
        """Multiple matches without replace_all returns error."""
        target = tmp_workspace / "multi.py"
        target.write_text("x = 1\ny = 1\nz = 1\n", encoding="utf-8")

        result = await file_edit(
            SearchReplaceInput(
                file_path=str(target),
                old_string="= 1",
                new_string="= 2",
            )
        )

        assert result.is_error is True
        assert "3" in result.output or "multiple" in result.output.lower()

    async def test_replace_all(self, tmp_workspace: Path):
        """replace_all=True replaces all occurrences."""
        target = tmp_workspace / "all.py"
        target.write_text("a = 1\nb = 1\nc = 1\n", encoding="utf-8")

        result = await file_edit(
            SearchReplaceInput(
                file_path=str(target),
                old_string="= 1",
                new_string="= 99",
                replace_all=True,
            )
        )

        assert result.is_error is False
        content = target.read_text(encoding="utf-8")
        assert content.count("= 99") == 3

    async def test_not_found_error(self, tmp_workspace: Path):
        """old_string not found returns error."""
        target = tmp_workspace / "exists.py"
        target.write_text("hello world\n", encoding="utf-8")

        result = await file_edit(
            SearchReplaceInput(
                file_path=str(target),
                old_string="nonexistent text",
                new_string="replacement",
            )
        )

        assert result.is_error is True

    async def test_edit_missing_file(self, tmp_workspace: Path):
        """Editing non-existent file returns error."""
        result = await file_edit(
            SearchReplaceInput(
                file_path=str(tmp_workspace / "ghost.py"),
                old_string="x",
                new_string="y",
            )
        )
        assert result.is_error is True


# ─── file_write ────────────────────────────────────────────────────────────────


class TestFileWrite:
    async def test_create_new_file(self, tmp_workspace: Path):
        """Write creates a new file with content."""
        target = tmp_workspace / "new_file.txt"

        result = await file_write(
            WriteFileInput(file_path=str(target), content="Hello World!")
        )

        assert result.is_error is False
        assert target.read_text(encoding="utf-8") == "Hello World!"

    async def test_overwrite_existing(self, tmp_workspace: Path):
        """Write overwrites existing file content."""
        target = tmp_workspace / "existing.txt"
        target.write_text("old content", encoding="utf-8")

        result = await file_write(
            WriteFileInput(file_path=str(target), content="new content")
        )

        assert result.is_error is False
        assert target.read_text(encoding="utf-8") == "new content"

    async def test_creates_parent_dirs(self, tmp_workspace: Path):
        """Write creates parent directories if they don't exist."""
        target = tmp_workspace / "sub" / "dir" / "file.txt"

        result = await file_write(
            WriteFileInput(file_path=str(target), content="nested")
        )

        assert result.is_error is False
        assert target.read_text(encoding="utf-8") == "nested"


# ─── shell_exec ────────────────────────────────────────────────────────────────


class TestShellExec:
    async def test_successful_command(self):
        """Simple echo command succeeds."""
        result = await shell_exec(ShellInput(command="echo hello"))

        assert result.is_error is False
        assert "hello" in result.output

    async def test_error_exit_code(self):
        """Command with non-zero exit code reports error."""
        # Use a command that fails on both Windows and POSIX
        result = await shell_exec(
            ShellInput(command="python -c \"import sys; sys.exit(1)\"")
        )

        assert result.is_error is True

    async def test_timeout(self):
        """Command exceeding timeout is killed."""
        result = await shell_exec(
            ShellInput(
                command="python -c \"import time; time.sleep(30)\"",
                timeout=2,
            )
        )

        assert result.is_error is True
        assert "timed out" in result.output.lower()

    async def test_workdir(self, tmp_workspace: Path):
        """Command runs in specified workdir."""
        result = await shell_exec(
            ShellInput(command="python -c \"import os; print(os.getcwd())\"",
                       workdir=str(tmp_workspace))
        )

        assert result.is_error is False
        assert str(tmp_workspace) in result.output or "workspace" in result.output


# ─── file_search ───────────────────────────────────────────────────────────────


class TestFileSearch:
    async def test_pattern_match(self, tmp_workspace: Path):
        """Search finds files containing pattern."""
        (tmp_workspace / "a.py").write_text("def hello():\n    pass\n", encoding="utf-8")
        (tmp_workspace / "b.py").write_text("def world():\n    pass\n", encoding="utf-8")

        result = await file_search(
            SearchInput(pattern="hello", path=str(tmp_workspace))
        )

        assert result.is_error is False
        assert "a.py" in result.output

    async def test_no_match(self, tmp_workspace: Path):
        """Search with no matches returns appropriate result."""
        (tmp_workspace / "a.py").write_text("nothing here\n", encoding="utf-8")

        result = await file_search(
            SearchInput(pattern="zzz_nonexistent_zzz", path=str(tmp_workspace))
        )

        # Should not be error, just empty/no results
        assert result.is_error is False

    async def test_case_insensitive(self, tmp_workspace: Path):
        """ignore_case=True matches regardless of case."""
        (tmp_workspace / "c.py").write_text("Hello World\n", encoding="utf-8")

        result = await file_search(
            SearchInput(
                pattern="hello world",
                path=str(tmp_workspace),
                ignore_case=True,
            )
        )

        assert result.is_error is False
        assert "c.py" in result.output

    async def test_include_glob_filter(self, tmp_workspace: Path):
        """include parameter filters by file glob."""
        (tmp_workspace / "match.py").write_text("target\n", encoding="utf-8")
        (tmp_workspace / "skip.txt").write_text("target\n", encoding="utf-8")

        result = await file_search(
            SearchInput(
                pattern="target",
                path=str(tmp_workspace),
                include="*.py",
            )
        )

        assert result.is_error is False
        assert "match.py" in result.output
        assert "skip.txt" not in result.output
