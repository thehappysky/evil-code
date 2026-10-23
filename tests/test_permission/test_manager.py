# [Phase 4] 2026-07-29: Tests for permission/manager.py
"""Tests for PermissionManager pipeline."""

import pytest

from code_agent.permission.manager import Decision, PermissionManager


class TestPermissionManager:
    """Test 3-layer permission pipeline."""

    @pytest.fixture
    def pm(self) -> PermissionManager:
        """Create a default PermissionManager."""
        return PermissionManager()

    # ── YOLO mode ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_yolo_allow_all(self):
        """YOLO mode allows any tool."""
        pm = PermissionManager(yolo_mode=True)
        decision = await pm.decide("bash", {"command": "rm -rf /"})
        assert decision == Decision.ALLOW

    @pytest.mark.asyncio
    async def test_yolo_toggle(self, pm):
        """set_yolo toggles mode."""
        pm.set_yolo(True)
        assert pm.yolo_mode

        decision = await pm.decide("bash", {"command": "rm -rf /"})
        assert decision == Decision.ALLOW

        pm.set_yolo(False)
        assert not pm.yolo_mode

    # ── Safe command ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_safe_command_allowed(self, pm):
        """Safe shell commands are ALLOWed."""
        decision = await pm.decide("bash", {"command": "ls"})
        assert decision == Decision.ALLOW

        decision = await pm.decide("bash", {"command": "git status"})
        assert decision == Decision.ALLOW

    @pytest.mark.asyncio
    async def test_unsafe_command_not_allowed(self, pm):
        """Unsafe commands get ASK."""
        decision = await pm.decide("bash", {"command": "rm -rf /"})
        assert decision == Decision.ASK

    # ── Allow/Deny rules ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_allow_rule_overrides(self, pm):
        """Allow rules explicitly allow matched tools."""
        pm.add_allow_rule("Bash(npm*)")
        # npm install is covered by explicit allow rule
        decision = await pm.decide("bash", {"command": "npm install express"})
        assert decision == Decision.ALLOW

    @pytest.mark.asyncio
    async def test_deny_rule_blocks(self, pm):
        """Deny rules block even safe commands."""
        pm.add_deny_rule("Bash(rm*)")
        decision = await pm.decide("bash", {"command": "rm file.txt"})
        assert decision == Decision.DENY

    @pytest.mark.asyncio
    async def test_deny_rule_higher_priority_than_allow(self, pm):
        """Deny rules take priority over allow rules."""
        pm.add_allow_rule("Bash(npm*)")
        pm.add_deny_rule("Bash(npm install*)")

        # Deny should win: "npm install*" matches "npm install dangerous-pkg"
        decision = await pm.decide("bash", {"command": "npm install dangerous-pkg"})
        assert decision == Decision.DENY

    @pytest.mark.asyncio
    async def test_edit_allow_rule(self, pm):
        """Edit allow rules cover file operations."""
        pm.add_allow_rule("Edit(**/*.py)")

        decision = await pm.decide("file_edit", {"file_path": "src/main.py"})
        assert decision == Decision.ALLOW

        decision = await pm.decide("file_edit", {"file_path": "README.md"})
        assert decision == Decision.ASK  # Not covered by rule

    # ── File operations ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_file_edit_default_asks(self, pm):
        """File edits not covered by rules get ASK by default."""
        decision = await pm.decide("file_edit", {"file_path": "src/main.py"})
        assert decision == Decision.ASK

    @pytest.mark.asyncio
    async def test_read_tools_pre_approved(self, pm):
        """Read tools are pre-approved (Rust layer-7: Read/Grep/WebSearch)."""
        for tool, args in [
            ("file_read", {"file_path": "src/main.py"}),
            ("file_search", {"pattern": "TODO"}),
            ("file_glob", {"pattern": "**/*.py"}),
            ("web_fetch", {"url": "https://example.com"}),
            ("web_search", {"query": "python"}),
        ]:
            assert await pm.decide(tool, args) == Decision.ALLOW, tool

    @pytest.mark.asyncio
    async def test_control_tools_pre_approved(self, pm):
        """Agent-owned control tools are pre-approved (safe by construction)."""
        for tool, args in [
            ("todo_write", {"todos": []}),
            ("exit_plan_mode", {"plan": "do it"}),
            ("skill", {"name": "demo"}),
        ]:
            assert await pm.decide(tool, args) == Decision.ALLOW, tool

    # ── Prompt policy (fail-closed) ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_prompt_policy_deny_is_fail_closed(self):
        """prompt_policy="deny" turns any ASK into DENY (dontAsk)."""
        pm = PermissionManager(prompt_policy="deny")
        # Unsafe shell command → DENY instead of ASK
        assert await pm.decide("bash", {"command": "rm -rf /"}) == Decision.DENY
        # Un-rule'd file edit → DENY instead of ASK
        assert await pm.decide("file_edit", {"file_path": "x.py"}) == Decision.DENY
        # But safe commands and read tools remain ALLOW
        assert await pm.decide("bash", {"command": "ls"}) == Decision.ALLOW
        assert await pm.decide("file_read", {"file_path": "x.py"}) == Decision.ALLOW
        # [2026-08-05] New read/control tools remain usable in headless.
        assert await pm.decide("file_glob", {"pattern": "*.py"}) == Decision.ALLOW
        assert await pm.decide("exit_plan_mode", {"plan": "x"}) == Decision.ALLOW
        # But sub-agent spawning is still gated (spawns LLM calls / cost).
        assert await pm.decide("task", {"prompt": "x"}) == Decision.DENY

    @pytest.mark.asyncio
    async def test_prompt_policy_deny_respects_allow_rules(self):
        """Allow rules still override prompt_policy=deny."""
        pm = PermissionManager(prompt_policy="deny")
        pm.add_allow_rule("Edit(**/*.py)")
        assert await pm.decide("file_edit", {"file_path": "src/main.py"}) == Decision.ALLOW

    def test_set_prompt_policy(self):
        """set_prompt_policy toggles and validates."""
        pm = PermissionManager()
        pm.set_prompt_policy("deny")
        assert pm.prompt_policy == "deny"
        with pytest.raises(ValueError):
            pm.set_prompt_policy("bogus")

    # ── Rules management ───────────────────────────────────────────────

    def test_add_allow_rule(self, pm):
        """add_allow_rule adds to the list."""
        pm.add_allow_rule("Bash(npm*)")
        assert "Bash(npm*)" in pm.allow_rules

    def test_add_deny_rule(self, pm):
        """add_deny_rule adds to the list."""
        pm.add_deny_rule("Bash(rm*)")
        assert "Bash(rm*)" in pm.deny_rules

    def test_duplicate_rules_not_added(self, pm):
        """Duplicate rules are not added twice."""
        pm.add_allow_rule("Bash(npm*)")
        pm.add_allow_rule("Bash(npm*)")
        assert len(pm.allow_rules) == 1
