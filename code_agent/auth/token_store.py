"""Token file read/write with atomic persistence.

Logic reference: Rust version JSONL storage atomic write pattern
(storage/jsonl/mod.rs:249).

Reads/writes Claude Code compatible credentials file:
    ~/.claude/.credentials.json

Dependencies: stdlib only (json, os, pathlib, tempfile).
Does NOT depend on any project-internal module.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# Claude Code compatible credentials path
_CREDS_DIR = Path.home() / ".claude"
_CREDS_FILE = _CREDS_DIR / ".credentials.json"

# The key under which OAuth token is stored in the credentials file
_OAUTH_KEY = "claudeAiOauth"


def load_token() -> dict | None:
    """Read OAuth token from credentials file.

    Returns:
        Token dict if file exists and contains valid claudeAiOauth data.
        None if file doesn't exist, is unreadable, or key is missing.
    """
    try:
        raw = _CREDS_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None

    token = data.get(_OAUTH_KEY)
    if not isinstance(token, dict):
        return None

    # Minimal validity check: must have accessToken
    if not token.get("accessToken"):
        return None

    return token


def save_token(token: dict) -> None:
    """Atomically write token to credentials file.

    Atomic write sequence: temp file -> json.dump -> flush -> fsync
    -> os.replace -> chmod 0o600.

    Preserves other top-level keys in the credentials file (e.g. mcpOAuth).
    Only replaces the claudeAiOauth key.

    Args:
        token: Token dict to persist.
    """
    # Read existing file to preserve other keys
    existing: dict = {}
    try:
        raw = _CREDS_FILE.read_text(encoding="utf-8")
        existing = json.loads(raw)
        if not isinstance(existing, dict):
            existing = {}
    except (OSError, json.JSONDecodeError):
        pass

    # Update only the OAuth key
    existing[_OAUTH_KEY] = token

    # Ensure directory exists
    _CREDS_DIR.mkdir(parents=True, exist_ok=True)

    # Atomic write: temp file in same directory -> os.replace
    fd, tmp_path = tempfile.mkstemp(
        dir=str(_CREDS_DIR), prefix=".creds_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(_CREDS_FILE))
    except BaseException:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    # Restrict permissions (owner read/write only)
    try:
        os.chmod(str(_CREDS_FILE), 0o600)
    except OSError:
        # Windows may not support chmod fully — non-fatal
        pass
