# [Phase 7] 2026-08-06: Draft（git worktree）测试。
"""Tests for code_agent/sandbox/draft.py with real git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from code_agent.sandbox.draft import DraftError, DraftManager


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "test")
    _git(root, "config", "user.email", "test@example.com")
    (root / "demo.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")


@pytest.mark.asyncio
async def test_create_requires_git_repo(tmp_path):
    (tmp_path / "not-a-repo").mkdir()
    mgr = DraftManager(
        drafts_dir=tmp_path / "drafts",
        state_path=tmp_path / "drafts.json",
    )
    with pytest.raises(DraftError):
        await mgr.create(str(tmp_path / "not-a-repo"))


@pytest.mark.asyncio
async def test_create_rejects_dirty_main(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "dirty.txt").write_text("x\n", encoding="utf-8")
    mgr = DraftManager(
        drafts_dir=tmp_path / "drafts",
        state_path=tmp_path / "drafts.json",
    )
    with pytest.raises(DraftError, match="未提交改动"):
        await mgr.create(str(repo))


@pytest.mark.asyncio
async def test_create_modify_merge_roundtrip(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    mgr = DraftManager(
        drafts_dir=tmp_path / "drafts",
        state_path=tmp_path / "drafts.json",
    )

    record = await mgr.create(str(repo))
    draft_root = Path(record.root)
    (draft_root / "demo.txt").write_text("base\nchanged\n", encoding="utf-8")
    (draft_root / "new.txt").write_text("new\n", encoding="utf-8")

    st = await mgr.status(record.draft_id)
    assert "demo.txt" in st["changed"]
    assert "new.txt" in st["changed"]

    result = await mgr.merge(record.draft_id)
    assert "merged" in result
    assert (repo / "demo.txt").read_text(encoding="utf-8") == "base\nchanged\n"
    assert (repo / "new.txt").read_text(encoding="utf-8") == "new\n"
    assert mgr.get(record.draft_id).merged is True


@pytest.mark.asyncio
async def test_merge_without_changes_is_noop(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    mgr = DraftManager(
        drafts_dir=tmp_path / "drafts",
        state_path=tmp_path / "drafts.json",
    )
    record = await mgr.create(str(repo))
    result = await mgr.merge(record.draft_id)
    assert result == "no changes to merge"


@pytest.mark.asyncio
async def test_discard_removes_worktree_and_branch(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    mgr = DraftManager(
        drafts_dir=tmp_path / "drafts",
        state_path=tmp_path / "drafts.json",
    )
    record = await mgr.create(str(repo))
    draft_root = Path(record.root)
    assert draft_root.exists()

    result = await mgr.discard(record.draft_id)
    assert "discarded" in result
    assert not draft_root.exists()
    branches = _git(repo, "branch", "--list", record.branch).stdout.strip()
    assert record.branch not in branches
    assert mgr.list_drafts() == []
