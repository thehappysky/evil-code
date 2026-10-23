"""Settings writer — read/write/clear ~/.claude/settings.json env block.

This is the evil-code equivalent of cc-switch's config generation.
Instead of manually editing JSON, users run:
    code-agent config set --api-key sk-xxx --base-url URL --model MODEL

The writer merges with the existing settings.json — non-env keys
(model, theme, etc.) are preserved. Only the env block's auth-related
keys are modified.

Compatible with cc-switch Tauri: same file, same format, same keys.
evil-code reads this file via auth/settings_store.py at runtime.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import NamedTuple

# ─── Constants ─────────────────────────────────────────────────────────────────

_DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# Auth env keys (only these are managed by config set/clear)
_AUTH_TOKEN_KEY = "ANTHROPIC_AUTH_TOKEN"
_API_KEY_KEY = "ANTHROPIC_API_KEY"
_BASE_URL_KEY = "ANTHROPIC_BASE_URL"
_MODEL_KEY = "ANTHROPIC_MODEL"

_MANAGED_KEYS = {_AUTH_TOKEN_KEY, _API_KEY_KEY, _BASE_URL_KEY, _MODEL_KEY}


class ConfigSummary(NamedTuple):
    """Summary of current settings.json auth config."""

    api_key: str | None
    base_url: str | None
    model: str | None
    auth_token_present: bool
    api_key_present: bool
    is_configured: bool
    settings_path: Path


# ─── Save (write/merge) ───────────────────────────────────────────────────────


def save_settings_config(
    api_key: str,
    base_url: str,
    model: str,
    *,
    use_auth_token: bool = True,
    settings_path: Path | None = None,
) -> Path:
    """Write provider config to settings.json env block.

    Merges with existing file — non-env keys (model, theme, etc.) are
    preserved. Only auth-related env vars are modified.

    Args:
        api_key: API key (e.g. sk-xxx).
        base_url: API base URL (e.g. https://api.deepseek.com/anthropic).
        model: Model ID (e.g. deepseek-v4-pro).
        use_auth_token: If True, write as ANTHROPIC_AUTH_TOKEN (Bearer auth).
            If False, write as ANTHROPIC_API_KEY (x-api-key auth).
        settings_path: Custom path (for testing). Default: ~/.claude/settings.json.

    Returns:
        Path to the written file.
    """
    if settings_path is None:
        settings_path = _DEFAULT_SETTINGS_PATH

    # Read existing settings (preserve non-env keys)
    settings = _read_settings(settings_path)

    # Ensure env block exists
    env = settings.setdefault("env", {})

    # Clear conflicting auth key first
    env.pop(_AUTH_TOKEN_KEY, None)
    env.pop(_API_KEY_KEY, None)

    # Write new config
    auth_key = _AUTH_TOKEN_KEY if use_auth_token else _API_KEY_KEY
    env[auth_key] = api_key
    env[_BASE_URL_KEY] = base_url
    env[_MODEL_KEY] = model

    # Atomic write (temp file + rename — same pattern as token_store.py)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(settings_path, settings)

    return settings_path


# ─── Show (read + display) ─────────────────────────────────────────────────────


def show_settings_config(settings_path: Path | None = None) -> ConfigSummary:
    """Read settings.json and return a summary of current auth config.

    Does NOT print — caller decides how to display.
    """
    if settings_path is None:
        settings_path = _DEFAULT_SETTINGS_PATH

    settings = _read_settings(settings_path)
    env = settings.get("env", {})

    auth_token = env.get(_AUTH_TOKEN_KEY)
    api_key_var = env.get(_API_KEY_KEY)

    return ConfigSummary(
        api_key=auth_token or api_key_var,
        base_url=env.get(_BASE_URL_KEY),
        model=env.get(_MODEL_KEY),
        auth_token_present=auth_token is not None,
        api_key_present=api_key_var is not None,
        is_configured=(auth_token or api_key_var) is not None,
        settings_path=settings_path,
    )


# ─── Clear (remove auth keys) ─────────────────────────────────────────────────


def clear_settings_config(settings_path: Path | None = None) -> Path:
    """Remove auth-related env vars from settings.json.

    Only removes ANTHROPIC_AUTH_TOKEN, ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL,
    ANTHROPIC_MODEL. Other env vars and non-env keys are preserved.

    Returns:
        Path to the modified file.
    """
    if settings_path is None:
        settings_path = _DEFAULT_SETTINGS_PATH

    settings = _read_settings(settings_path)
    env = settings.get("env", {})

    for key in _MANAGED_KEYS:
        env.pop(key, None)

    # Clean up empty env block
    if not env:
        settings.pop("env", None)

    _atomic_write_json(settings_path, settings)

    return settings_path


# ─── Helpers ───────────────────────────────────────────────────────────────────


def _read_settings(path: Path) -> dict:
    """Read settings.json, return empty dict if missing or invalid."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically: temp file → fsync → rename."""
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=".settings_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _mask_key(key: str) -> str:
    """Mask API key for display: sk-8635... → sk-8635e1c9..."""
    if not key or len(key) <= 12:
        return "***" if key else ""
    return f"{key[:8]}...{key[-4:]}"
