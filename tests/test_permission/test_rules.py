# [Phase 4] 2026-07-29: Tests for permission/rules.py
"""Tests for rule pattern matcher."""

import pytest

from code_agent.permission.rules import match_rule


class TestMatchRule:
    """Test match_rule function."""

    # ── Bash rules ─────────────────────────────────────────────────────

    def test_bash_npm_prefix(self):
        """Bash(npm*) matches npm commands."""
        assert match_rule("Bash(npm*)", "bash", {"command": "npm install"})
        assert match_rule("Bash(npm*)", "bash", {"command": "npm test"})

    def test_bash_npm_prefix_non_match(self):
        """Bash(npm*) doesn't match other commands."""
        assert not match_rule("Bash(npm*)", "bash", {"command": "rm file"})

    def test_bash_bare_prefix(self):
        """Bash (no spec) matches any bash call."""
        assert match_rule("Bash", "bash", {"command": "any_command"})

    def test_bash_rm_deny(self):
        """Bash(rm*) matches destructive commands."""
        assert match_rule("Bash(rm*)", "bash", {"command": "rm file"})
        assert match_rule("Bash(rm*)", "bash", {"command": "rm -rf /"})

    # ── Edit rules ─────────────────────────────────────────────────────

    def test_edit_py_glob(self):
        """Edit(**/*.py) matches Python file edits."""
        assert match_rule("Edit(**/*.py)", "file_edit", {"file_path": "src/main.py"})
        assert match_rule("Edit(**/*.py)", "file_write", {"file_path": "test/test_app.py"})

    def test_edit_py_glob_non_match(self):
        """Edit(**/*.py) doesn't match non-Python files."""
        assert not match_rule("Edit(**/*.py)", "file_edit", {"file_path": "README.md"})

    # ── WebFetch rules ─────────────────────────────────────────────────

    def test_webfetch_domain(self):
        """WebFetch(domain:github.com) matches GitHub URLs."""
        assert match_rule(
            "WebFetch(domain:github.com)", "web_fetch",
            {"url": "https://github.com/user/repo"},
        )
        assert not match_rule(
            "WebFetch(domain:github.com)", "web_fetch",
            {"url": "https://google.com"},
        )

    # ── Alias mappings ─────────────────────────────────────────────────

    def test_edit_alias_covers_file_edit_and_write(self):
        """'Edit' prefix covers both file_edit and file_write."""
        # Use *.py which matches "a.py" directly
        assert match_rule("Edit(*.py)", "file_edit", {"file_path": "a.py"})
        assert match_rule("Edit(*.py)", "file_write", {"file_path": "a.py"})
        assert match_rule("Edit(*.py)", "search_replace", {"file_path": "a.py"})

    def test_read_alias_covers_file_read(self):
        """'Read' prefix covers file_read."""
        assert match_rule("Read(*)", "file_read", {"file_path": "test.py"})

    def test_read_alias_covers_file_search_path_key(self):
        """'Read' rules must match file_search via its 'path' argument key.

        file_search uses the argument key 'path', not 'file_path'; a rule with
        a match spec was previously a no-op against it.
        """
        assert match_rule(
            "Read(**/*.py)", "file_search", {"path": "src/main.py", "pattern": "x"}
        )
        assert not match_rule(
            "Read(**/*.py)", "file_search", {"path": "src/main.log"}
        )
        # file_path-based tools still match via their own key.
        assert match_rule(
            "Read(**/*.py)", "file_search", {"file_path": "src/main.py"}
        )

    def test_file_search_bare_rule_matches_without_spec(self):
        """A bare 'Read' prefix matches any file_search call."""
        assert match_rule("Read", "file_search", {"path": "anything", "pattern": "x"})

    # ── Edge cases ─────────────────────────────────────────────────────

    def test_case_insensitive_tool_name(self):
        """Tool name matching is case-insensitive."""
        assert match_rule("bash(npm*)", "BASH", {"command": "npm test"})

    def test_no_match_with_wrong_tool(self):
        """Rule for Bash doesn't match Edit tools."""
        assert not match_rule("Bash(npm*)", "file_edit", {"file_path": "npm.js"})

    def test_unknown_tool_returns_false(self):
        """Unknown tool type returns False."""
        # Bare prefix without match_spec also needs tool match
        assert not match_rule("Bash", "unknown_tool", {})
def test_delete_rule_matches_target_path():
    from code_agent.permission.rules import match_rule

    assert match_rule("file_delete(**/*.env)", "file_delete", {"file_path": "/project/prod.env"})
    assert not match_rule("file_delete(**/*.env)", "file_delete", {"file_path": "/project/readme.md"})

