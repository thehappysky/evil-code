# [Phase 4] 2026-07-29: Permission system package
# Public API: PermissionManager, Decision, is_safe_command, match_rule

from code_agent.permission.safe_commands import SAFE_COMMANDS, is_safe_command
from code_agent.permission.rules import match_rule


def __getattr__(name: str):
    # The sandbox policy imports safe_commands. Loading its package must not
    # eagerly import the manager, which itself consumes the sandbox policy.
    if name in {"Decision", "PermissionManager"}:
        from code_agent.permission.manager import Decision, PermissionManager
        return {"Decision": Decision, "PermissionManager": PermissionManager}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "PermissionManager",
    "Decision",
    "SAFE_COMMANDS",
    "is_safe_command",
    "match_rule",
]
