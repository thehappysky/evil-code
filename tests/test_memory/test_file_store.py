"""Tests for memory/file_store.py — MemoryStore."""

from pathlib import Path

import pytest

from code_agent.memory.file_store import MemoryStore, _make_slug, _project_hash


class TestMakeSlug:
    def test_basic(self):
        assert _make_slug("Hello World") == "hello-world"

    def test_max_len(self):
        slug = _make_slug("This is a very long text that exceeds the limit", max_len=10)
        assert len(slug) <= 10

    def test_special_chars(self):
        slug = _make_slug("Hello! @World #2024")
        assert "!" not in slug
        assert "@" not in slug
        assert "#" not in slug

    def test_empty(self):
        slug = _make_slug("!!!")
        assert slug in ("session", "")


class TestProjectHash:
    def test_deterministic(self):
        h1 = _project_hash("/test/path")
        h2 = _project_hash("/test/path")
        assert h1 == h2

    def test_length(self):
        h = _project_hash("/test")
        assert len(h) == 12


class TestMemoryStore:
    @pytest.fixture
    def store(self, tmp_path):
        return MemoryStore(root_dir=tmp_path / "memory")

    @pytest.mark.asyncio
    async def test_read_write_global(self, store):
        await store.write_global("# Global\n\n## Preferences\nDark mode.")
        content = await store.read_global()
        assert "Dark mode" in content

    @pytest.mark.asyncio
    async def test_read_global_empty(self, store):
        content = await store.read_global()
        assert content == ""

    @pytest.mark.asyncio
    async def test_read_write_workspace(self, store):
        await store.write_workspace("/project", "# Project\n\n## Rules\nUse PEP 8.")
        content = await store.read_workspace("/project")
        assert "PEP 8" in content

    @pytest.mark.asyncio
    async def test_read_workspace_empty(self, store):
        content = await store.read_workspace("/nonexistent")
        assert content == ""

    @pytest.mark.asyncio
    async def test_append_session_log(self, store):
        await store.append_session_log(
            "/project", "s_20260725_abc12345",
            "Implement feature X",
            "## Summary\nWorked on feature X. Made good progress.",
        )
        # Session log directory is under the workspace hash directory
        ws_dir = store.workspace_dir("/project")
        sessions_dir = ws_dir / "sessions"
        assert sessions_dir.exists()

        # Should have created a log file
        log_files = list(sessions_dir.glob("*.md"))
        assert len(log_files) >= 1

    @pytest.mark.asyncio
    async def test_workspace_dir_consistency(self, store):
        ws_dir = store.workspace_dir("/a")
        ws_dir2 = store.workspace_dir("/a")
        assert ws_dir == ws_dir2

    @pytest.mark.asyncio
    async def test_different_workspaces(self, store):
        await store.write_workspace("/proj1", "Content 1")
        await store.write_workspace("/proj2", "Content 2")

        c1 = await store.read_workspace("/proj1")
        c2 = await store.read_workspace("/proj2")

        assert c1 != c2
