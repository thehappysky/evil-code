# [Phase 4] 2026-08-02: Tests for dangerous-flag enforcement + rule priority
"""Tests for dangerous-flag enforcement and deny/allow rule priority.

Covers:
- ``_DANGEROUS_FLAGS`` enforced via ``is_safe_command`` (rg --pre, sed -i,
  find -delete/-exec, git push/commit/add).
- Safe subcommands still pass (git status/log/diff/rev-parse, sed -n, find -name).
- Manager priority: deny beats YOLO and beats allow; file-tool deny/allow rules.
"""

import pytest

from code_agent.permission.manager import Decision, PermissionManager
from code_agent.permission.safe_commands import is_safe_command


class TestDangerousFlags:
    """is_safe_command must reject commands with dangerous flags."""

    @pytest.mark.parametrize("cmd", [
        "rg --pre foo",
        "rg --pre=cat foo",
        "rg -n --pre python3 foo",
    ])
    def test_rg_pre_not_safe(self, cmd):
        """rg --pre can execute arbitrary programs — never safe."""
        assert not is_safe_command(cmd), f"Expected not safe: {cmd}"

    @pytest.mark.parametrize("cmd", [
        "sed -i s/a/b/ file",
        "sed -i.bak s/a/b/ file",
        "sed --in-place s/a/b/ file",
        "sed --in-place= '.bak' s/a/b/ file",
    ])
    def test_sed_inplace_not_safe(self, cmd):
        """sed -i / --in-place modify files in place — never safe."""
        assert not is_safe_command(cmd), f"Expected not safe: {cmd}"

    @pytest.mark.parametrize("cmd", [
        "find . -delete",
        "find . -name '*.py' -delete",
        "find . -exec rm {} ;",
        "find . -exec echo hi",
    ])
    def test_find_destructive_not_safe(self, cmd):
        """find -delete / -exec are destructive — never safe."""
        assert not is_safe_command(cmd), f"Expected not safe: {cmd}"

    @pytest.mark.parametrize("cmd", [
        "git push",
        "git push origin main",
        "git commit",
        "git commit -m 'x'",
        "git add",
        "git add .",
    ])
    def test_git_write_subcommands_not_safe(self, cmd):
        """git push/commit/add write to the repository — never safe."""
        assert not is_safe_command(cmd), f"Expected not safe: {cmd}"


class TestSafeSubcommands:
    """Safe read-only subcommands of dangerous-flag tools must still pass."""

    @pytest.mark.parametrize("cmd", [
        "git status",
        "git status --porcelain",
        "git log",
        "git log --oneline -5",
        "git diff",
        "git diff HEAD~1",
        "git rev-parse HEAD",
    ])
    def test_safe_git_read_only(self, cmd):
        """Git read-only subcommands remain safe despite the git deny list."""
        assert is_safe_command(cmd), f"Expected safe: {cmd}"

    @pytest.mark.parametrize("cmd", [
        "sed -n '1,10p' file",
        "sed -n '1,10p' 'a b' file",
    ])
    def test_sed_n_no_inplace(self, cmd):
        """sed -n (print range) is read-only and safe."""
        assert is_safe_command(cmd), f"Expected safe: {cmd}"

    def test_find_name_no_destructive_flag(self):
        """find with -name but no -delete/-exec is safe."""
        assert is_safe_command("find . -name '*.py'")


class TestManagerPriority:
    """Deny rules beat YOLO and allow rules; file-tool rules apply."""

    @pytest.mark.asyncio
    async def test_deny_rule_beats_yolo(self):
        """yolo_mode=True still returns DENY when a deny rule matches.

        The pipeline checks deny rules *before* YOLO mode, so YOLO cannot
        override an explicit deny.
        """
        pm = PermissionManager(yolo_mode=True, deny_rules=["Bash(rm*)"])
        decision = await pm.decide("bash", {"command": "rm -rf /"})
        assert decision == Decision.DENY

    @pytest.mark.asyncio
    async def test_deny_rule_beats_allow_bash(self):
        """For bash, a matching deny rule wins over a broader allow rule."""
        pm = PermissionManager(
            allow_rules=["Bash(git*)"],
            deny_rules=["Bash(git push*)"],
        )
        decision = await pm.decide("bash", {"command": "git push origin main"})
        assert decision == Decision.DENY

    @pytest.mark.asyncio
    async def test_allow_rule_applies_when_no_deny_bash(self):
        """Allow rule ALLOWs bash calls when no deny rule matches."""
        pm = PermissionManager(
            allow_rules=["Bash(git*)"],
            deny_rules=["Bash(rm*)"],
        )
        decision = await pm.decide("bash", {"command": "git status"})
        assert decision == Decision.ALLOW

    @pytest.mark.asyncio
    async def test_file_deny_rule_blocks_log(self):
        """Edit(**/*.log) deny rule blocks file_edit on a .log path."""
        pm = PermissionManager(deny_rules=["Edit(**/*.log)"])
        decision = await pm.decide("file_edit", {"file_path": "logs/app.log"})
        assert decision == Decision.DENY

    @pytest.mark.asyncio
    async def test_file_deny_rule_does_not_block_other(self):
        """A .log deny rule does not touch non-.log file edits (ASK default)."""
        pm = PermissionManager(deny_rules=["Edit(**/*.log)"])
        decision = await pm.decide("file_edit", {"file_path": "src/main.py"})
        assert decision == Decision.ASK

    @pytest.mark.asyncio
    async def test_file_allow_rule_allows_py(self):
        """Edit(**/*.py) allow rule ALLOWs file_edit on a .py path."""
        pm = PermissionManager(allow_rules=["Edit(**/*.py)"])
        decision = await pm.decide("file_edit", {"file_path": "src/main.py"})
        assert decision == Decision.ALLOW

    @pytest.mark.asyncio
    async def test_file_allow_rule_asks_for_other_paths(self):
        """Edit(**/*.py) allow rule does not cover non-.py paths (ASK)."""
        pm = PermissionManager(allow_rules=["Edit(**/*.py)"])
        decision = await pm.decide("file_edit", {"file_path": "README.md"})
        assert decision == Decision.ASK

    @pytest.mark.asyncio
    async def test_file_deny_beats_file_allow(self):
        """For file tools, deny still beats allow: .log denied, .py allowed."""
        pm = PermissionManager(
            allow_rules=["Edit(**/*.py)"],
            deny_rules=["Edit(**/*.log)"],
        )
        denied = await pm.decide("file_edit", {"file_path": "app.log"})
        allowed = await pm.decide("file_edit", {"file_path": "app.py"})
        assert denied == Decision.DENY
        assert allowed == Decision.ALLOW
