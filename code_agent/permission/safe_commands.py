# [Phase 4] 2026-07-29: Safe command whitelist — commands always safe to execute
"""Safe command whitelist.

References:
    - design-docs/Python-Agent-MVP-Phase4-详细设计.md §2 permission/safe_commands.py
    - Rust is_safe_command_words() (permission/manager.rs:167)

Commands that are ALWAYS safe to execute without asking.
Explicitly excludes dangerous variants (e.g., rg --pre, tee, destructive kubectl).
"""

from __future__ import annotations

import shlex

# ── Safe command list ────────────────────────────────────────────────────────

SAFE_COMMANDS: list[str] = [
    # ── Read-only filesystem (Unix) ──────────────────────────────────────
    "ls", "cat", "pwd", "date", "whoami", "hostname", "uptime",
    "uname", "which", "whereis", "file", "stat", "du", "df",
    "readlink", "realpath", "basename", "dirname",

    # ── Read-only filesystem (Windows) ───────────────────────────────────
    "dir", "type", "where", "cd", "chdir",

    # ── Git read-only ────────────────────────────────────────────────────
    "git status", "git branch", "git log", "git diff",
    "git show", "git rev-parse", "git ls-files", "git remote",
    "git tag", "git stash list", "git config --list",
    "git blame", "git grep", "git shortlog",

    # ── Package manager read-only ───────────────────────────────────────
    "cargo check", "npm ls", "npm list", "npm outdated",
    "pip list", "pip show", "pip freeze",
    "poetry show", "poetry check",

    # ── Text processing (Unix) ──────────────────────────────────────────
    "head", "tail", "wc", "sort", "uniq", "tr", "cut",
    "awk", "sed -n", "column", "paste", "join",

    # ── Text processing (Windows) ───────────────────────────────────────
    "findstr",

    # ── Search tools ─────────────────────────────────────────────────────
    "grep", "rg", "find",

    # ── Kubernetes read-only ─────────────────────────────────────────────
    "kubectl get", "kubectl logs", "kubectl describe",
    "kubectl explain", "kubectl api-resources", "kubectl api-versions",
    "kubectl cluster-info", "kubectl config view", "kubectl top",

    # ── Process inspection (Unix + Windows) ─────────────────────────────
    "ps", "tasklist",

    # ── Environment inspection ───────────────────────────────────────────
    "echo", "env", "printenv", "set",
]

# Explicitly NOT safe — these can bypass security or cause destruction:
# - "rg --pre" — can execute arbitrary programs
# - "tee" — can write to arbitrary files (CWE-863 bypass)
# - "sed -i" — in-place file modification
# - Any destructive kubectl: apply, delete, create, edit, patch, replace, rollout
# - "chmod", "chown" — permission changes
# - "rm", "mv", "cp" — file operations (require permission prompt)

_DANGEROUS_FLAGS: dict[str, list[str]] = {
    "rg": ["--pre"],
    "sed": ["-i", "--in-place"],
    "grep": [],  # grep is always safe (read-only)
    "find": ["-delete", "-exec"],
    "git": ["push", "commit", "add"],  # These subcommands write
}

def _has_shell_escape(command: str) -> bool:
    """Quote-aware scan for shell operators that execute extra logic.

    A whitelisted command must be a single command with plain literal
    arguments. Returns True if the raw command uses chaining, piping,
    redirection, or substitution that would execute logic beyond that first
    command.

    Quote rules:
    - Metacharacters inside single quotes are literal and ignored.
    - Inside double quotes ``; | < > &`` are literal, but command substitution
      (``$(...)``, backticks) is still active and is flagged.
    - Backslash escapes skip the next character.
    """
    state = "none"  # none | single | double | escaped
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if state == "escaped":
            state = "none"
            i += 1
            continue
        if state == "single":
            if ch == "'":
                state = "none"
            i += 1
            continue
        if state == "double":
            if ch == '"':
                state = "none"
            elif ch == "\\":
                state = "escaped"
            elif ch == "`":
                return True
            elif ch == "$" and i + 1 < n and command[i + 1] == "(":
                return True
            i += 1
            continue
        # state == "none"
        if ch == "\\":
            state = "escaped"
        elif ch == "'":
            state = "single"
        elif ch == '"':
            state = "double"
        elif ch in ";|<>`&":
            return True
        elif ch == "$" and i + 1 < n and command[i + 1] == "(":
            return True
        i += 1
    return False


def is_safe_command(command: str) -> bool:
    """Check if a shell command matches a safe prefix.

    Parses command with shlex, checks prefix against SAFE_COMMANDS.
    Returns False for any command containing dangerous flags, command
    chaining/piping/redirection/substitution, or embedded newlines.

    Args:
        command: The shell command string to check.

    Returns:
        True if the command is in the safe whitelist and has no dangerous flags.
    """
    # Multiline input is multiple shell statements — never safe.
    if "\n" in command or "\r" in command:
        return False

    # Parse the command
    try:
        tokens = shlex.split(command)
    except ValueError:
        # Unparseable command → not safe
        return False

    if not tokens:
        return False

    # Reject chaining / piping / redirection / substitution outright.
    if _has_shell_escape(command):
        return False

    cmd_name = tokens[0]
    cmd_name_lower = cmd_name.lower()

    # Check for dangerous flags
    if cmd_name_lower in _DANGEROUS_FLAGS:
        dangerous = _DANGEROUS_FLAGS[cmd_name_lower]
        for flag in dangerous:
            if any(t.startswith(flag) or t == flag for t in tokens[1:]):
                return False

    # Normalize the command for prefix matching
    cmd_normalized = " ".join(tokens).strip()

    # Check against safe list
    for safe_cmd in SAFE_COMMANDS:
        if cmd_normalized == safe_cmd or cmd_normalized.startswith(safe_cmd + " "):
            return True

    return False
