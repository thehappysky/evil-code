# [Phase 4] 2026-07-29: PermissionManager — permission decision pipeline
# [2026-08-04] fail-closed alignment with Rust 7-layer pipeline
"""Permission decision pipeline.

References:
    - design-docs/Python-Agent-MVP-Phase4-详细设计.md §2 permission/manager.py
    - Rust 7-layer pipeline (permission/manager.rs), simplified for MVP

Pipeline checked in strict priority order (aligns with Rust manager.rs):
    1. Deny rules → DENY (highest priority, enforced before YOLO)
    2. YOLO mode → ALLOW (skip all further checks)
    3. Allow rules → ALLOW
    4. Safe command whitelist (bash/shell_exec) → ALLOW
    5. Read tools (file_read/file_search/web_fetch) → ALLOW (Rust layer-7:
       Read/Grep/WebSearch are pre-approved)
    6. prompt_policy == "deny" (dontAsk) → DENY  ← fail-closed for headless
    7. Default → ASK (interactive confirmation)

The `prompt_policy` gate mirrors Rust `PromptPolicy::Deny` (manager.rs:1517):
any tool that would need user confirmation is DENIED instead of silently
allowed when running non-interactively.
"""

from __future__ import annotations

import logging
import os
from enum import Enum

from code_agent.permission.rules import match_rule
from code_agent.permission.safe_commands import is_safe_command
from code_agent.sandbox.policy import classify

logger = logging.getLogger(__name__)


class Decision(Enum):
    """Permission decision result."""

    ALLOW = "allow"  # Execute without prompting
    DENY = "deny"    # Block execution entirely
    ASK = "ask"      # Prompt user for confirmation


# Read-only tools are pre-approved (Rust layer-7: Read/Grep/WebSearch → Allow).
# [2026-08-05] file_glob (directory listing) and web_search (Responses backend)
# are read-only too — they were previously missing and got denied in headless.
_READ_TOOL_NAMES = frozenset({
    "file_read",
    "file_search",
    "file_glob",
    "web_fetch",
    "web_search",
})

# Agent-owned control tools: they only mutate the agent's own state (todo
# list, plan-mode FSM) or return user-installed skill instructions — they do
# not touch the workspace or spawn external processes. Pre-approved so that
# fail-closed headless mode does not deadlock Plan Mode (exit_plan_mode) or
# disable harmless bookkeeping tools (todo_write / skill).
_CONTROL_TOOL_NAMES = frozenset({
    "todo_write",
    "exit_plan_mode",
    "skill",
})


class PermissionManager:
    """Permission decision pipeline.

    Usage::

        pm = PermissionManager(yolo_mode=False)
        decision = await pm.decide("bash", {"command": "ls"})
        # → Decision.ALLOW (safe command whitelist)

        decision = await pm.decide("bash", {"command": "rm -rf /"})
        # → Decision.ASK (not safe, needs user confirmation)
        #   with prompt_policy="deny" → Decision.DENY (fail-closed)
    """

    def __init__(
        self,
        yolo_mode: bool = False,
        allow_rules: list[str] | None = None,
        deny_rules: list[str] | None = None,
        prompt_policy: str = "ask",
        sandbox_mode: str = "off",
        sandbox_available=None,
        read_tools: frozenset[str] | None = None,
        control_tools: frozenset[str] | None = None,
    ):
        self._yolo_mode = yolo_mode
        self._allow_rules: list[str] = allow_rules or []
        self._deny_rules: list[str] = deny_rules or []
        # "ask" (default, interactive prompt) | "deny" (dontAsk, fail-closed)
        self._prompt_policy = prompt_policy
        # [Phase 7] 沙箱自动放行：mode != off 时，风险命令在沙箱可用时 ALLOW。
        self._sandbox_mode = sandbox_mode
        self._sandbox_available = sandbox_available
        # [GRD-03] Tool safety classification: from registry (authoritative,
        # passed by assembly.py) or hardcoded fallback (backward compat for
        # tests / non-assembly callers).
        self._read_tools = read_tools if read_tools is not None else _READ_TOOL_NAMES
        self._control_tools = control_tools if control_tools is not None else _CONTROL_TOOL_NAMES
        self.last_reason: str = ""

    def configure_sandbox(self, sandbox_mode: str, sandbox_available=None) -> None:
        """在 PermissionManager 构造后注入沙箱联动（manager 较晚创建时使用）。"""
        self._sandbox_mode = sandbox_mode
        self._sandbox_available = sandbox_available

    def configure_tool_safety(
        self,
        read_tools: frozenset[str] | None = None,
        control_tools: frozenset[str] | None = None,
    ) -> None:
        """[GRD-03] Backfill tool safety classification from the registry.

        Called after the registry is finalized (in build_agent) so the
        permission pipeline uses tool-declared safety instead of the
        hardcoded fallback.
        """
        if read_tools is not None:
            self._read_tools = read_tools
        if control_tools is not None:
            self._control_tools = control_tools

    # ── Main decision method ──────────────────────────────────────────────

    async def decide(self, tool_name: str, arguments: dict) -> Decision:
        """Check if a tool call needs user confirmation.

        Called before every tool execution in Agent._execute_one_tool().

        Pipeline order:
        1. Deny rules: if matched → DENY
        2. YOLO mode: if enabled → ALLOW
        3. Allow rules: if matched → ALLOW
        4. Safe command: if matched → ALLOW
        5. Read tools: → ALLOW (pre-approved)
        5b. Control tools (todo_write/exit_plan_mode/skill): → ALLOW
        6. prompt_policy == "deny": → DENY (fail-closed)
        7. Default: → ASK

        Args:
            tool_name: Name of the tool being called.
            arguments: Tool arguments dict.

        Returns:
            Decision.ALLOW, DENY, or ASK.
        """
        # 1. Explicit deny rules (highest priority after YOLO)
        for rule in self._deny_rules:
            if match_rule(rule, tool_name, arguments):
                logger.debug(
                    "Permission DENY by rule '%s': %s %s",
                    rule, tool_name,
                    _summarize_args(arguments),
                )
                return Decision.DENY

        # 2. YOLO mode
        if self._yolo_mode:
            logger.debug("Permission ALLOW by YOLO: %s", tool_name)
            return Decision.ALLOW

        # 3. Explicit allow rules
        for rule in self._allow_rules:
            if match_rule(rule, tool_name, arguments):
                logger.debug(
                    "Permission ALLOW by rule '%s': %s",
                    rule, tool_name,
                )
                return Decision.ALLOW

        # 4. Safe command whitelist
        if tool_name in ("bash", "shell_exec"):
            command = arguments.get("command", "")
            # [Phase 7] 沙箱联动：风险命令在沙箱可用时自动放行（隔离换免审批）。
            if self._sandbox_mode != "off":
                decision = classify(command, cwd=os.getcwd())
                if decision.is_sandbox:
                    available = False
                    if self._sandbox_available is not None:
                        available = await self._sandbox_available()
                    if available:
                        self.last_reason = (
                            f"sandbox_auto:{decision.tier}:{decision.reason}"
                        )
                        logger.debug(
                            "Permission ALLOW by sandbox auto: %s (%s)",
                            command, decision.tier,
                        )
                        return Decision.ALLOW
                    self.last_reason = (
                        f"sandbox_unavailable:{decision.tier}:{decision.reason}"
                    )
                    logger.info(
                        "Sandbox unavailable for %s (%s); falling back to ask/deny",
                        command, decision.tier,
                    )
                    # 继续走 fail-closed / 审批流程
            if is_safe_command(command):
                logger.debug("Permission ALLOW by safe command: %s", command)
                return Decision.ALLOW

        # 5. Read tools are pre-approved (Rust layer-7)
        if tool_name in self._read_tools:
            logger.debug("Permission ALLOW by read-tool pre-approval: %s", tool_name)
            return Decision.ALLOW

        # 5b. Agent-owned control tools are pre-approved (safe by construction).
        if tool_name in self._control_tools:
            logger.debug(
                "Permission ALLOW by control-tool pre-approval: %s", tool_name,
            )
            return Decision.ALLOW

        # 6. Fail-closed gate for non-interactive (Rust PromptPolicy::Deny)
        if self._prompt_policy == "deny":
            logger.info(
                "Permission DENY by prompt policy (tool not pre-approved): "
                "%s %s",
                tool_name, _summarize_args(arguments),
            )
            self.last_reason = self.last_reason or "prompt_policy_deny"
            return Decision.DENY

        # 7. Default: ask user
        logger.debug("Permission ASK (default): %s %s", tool_name,
                     _summarize_args(arguments))
        self.last_reason = self.last_reason or "requires_approval"
        return Decision.ASK

    # ── Configuration ────────────────────────────────────────────────────

    def set_yolo(self, enabled: bool) -> None:
        """Toggle YOLO mode.

        When enabled, all tools are auto-approved without user confirmation.
        """
        self._yolo_mode = enabled
        logger.info("YOLO mode %s", "enabled" if enabled else "disabled")

    def add_allow_rule(self, pattern: str) -> None:
        """Add an allow rule at runtime.

        Args:
            pattern: Rule pattern (e.g., "Bash(npm*)", "Edit(**/*.py)").
        """
        if pattern not in self._allow_rules:
            self._allow_rules.append(pattern)
            logger.debug("Allow rule added: %s", pattern)

    def add_deny_rule(self, pattern: str) -> None:
        """Add a deny rule at runtime.

        Args:
            pattern: Rule pattern (e.g., "Bash(rm*)", "Edit(**/*.log)").
        """
        if pattern not in self._deny_rules:
            self._deny_rules.append(pattern)
            logger.debug("Deny rule added: %s", pattern)

    @property
    def yolo_mode(self) -> bool:
        """Current YOLO mode state."""
        return self._yolo_mode

    @property
    def allow_rules(self) -> list[str]:
        """Current allow rules."""
        return list(self._allow_rules)

    @property
    def deny_rules(self) -> list[str]:
        """Current deny rules."""
        return list(self._deny_rules)

    @property
    def prompt_policy(self) -> str:
        """Current prompt policy ("ask" | "deny")."""
        return self._prompt_policy

    def set_prompt_policy(self, policy: str) -> None:
        """Set the prompt policy.

        "ask"  → tools needing confirmation return ASK (interactive prompt).
        "deny" → tools needing confirmation return DENY (fail-closed,
                 for headless / non-interactive modes).
        """
        if policy not in ("ask", "deny"):
            raise ValueError(f"Invalid prompt_policy: {policy}")
        self._prompt_policy = policy
        logger.info("Prompt policy set to %s", policy)


def _summarize_args(args: dict, max_len: int = 60) -> str:
    """Brief summary of arguments for logging.

    Args:
        args: Tool arguments dict.
        max_len: Maximum summary length.

    Returns:
        Truncated string representation.
    """
    keys = sorted(args.keys()) if args else []
    parts = []
    for k in keys:
        v = str(args[k])
        if len(v) > max_len:
            v = v[:max_len] + "..."
        parts.append(f"{k}={v}")
    return " ".join(parts)
