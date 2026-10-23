"""Read Claude Code / cc-switch settings.json env block.

This is the cc-switch integration point. The cc-switch Tauri app writes
provider configuration (API key, base URL, model) into ~/.claude/settings.json
under the "env" key. Claude Code reads these as environment variables at
startup. evil-code reads the same file for the same effect — no separate
cc-switch operation needed.

Settings.json format (cc-switch Tauri writes this):
    {
      "env": {
        "ANTHROPIC_AUTH_TOKEN": "sk-...",      # -> Bearer auth
        "ANTHROPIC_API_KEY": "sk-...",         # -> x-api-key auth (alternative)
        "ANTHROPIC_BASE_URL": "https://...",   # -> API endpoint
        "ANTHROPIC_MODEL": "deepseek-v4-pro"   # -> default model
      },
      ...other keys (permissions, theme, etc.)
    }

Priority: ANTHROPIC_AUTH_TOKEN (Bearer) > ANTHROPIC_API_KEY (XApiKey).
If neither is present, returns is_present=False.

Pure function module — does not modify files, holds no state.
Same design as auth/token_store.py.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import NamedTuple

from code_agent.api.schemas import AuthScheme

logger = logging.getLogger(__name__)

_DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


class SettingsCredentials(NamedTuple):
    """Credentials parsed from settings.json env block.

    Attributes:
        api_key: API key (from ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY).
        base_url: API endpoint URL (from ANTHROPIC_BASE_URL).
        model: Default model ID (from ANTHROPIC_MODEL).
        auth_scheme: Auth header scheme (Bearer or XApiKey).
        is_present: True if usable credentials were found.
    """

    api_key: str | None
    base_url: str | None
    model: str | None
    auth_scheme: AuthScheme | None
    is_present: bool


def load_settings_credentials(
    settings_path: Path | None = None,
) -> SettingsCredentials:
    """Read ~/.claude/settings.json env block and extract auth config.

    Priority within the file: ANTHROPIC_AUTH_TOKEN (Bearer) first,
    then ANTHROPIC_API_KEY (XApiKey).

    Args:
        settings_path: Custom settings.json path (for testing).
                       Default: ~/.claude/settings.json

    Returns:
        SettingsCredentials. If file missing or no auth env vars,
        returns is_present=False with all fields None.
    """
    if settings_path is None:
        settings_path = _DEFAULT_SETTINGS_PATH

    try:
        with open(settings_path, encoding="utf-8") as f:
            settings = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return SettingsCredentials(
            api_key=None,
            base_url=None,
            model=None,
            auth_scheme=None,
            is_present=False,
        )

    env = settings.get("env", {})

    auth_token = env.get("ANTHROPIC_AUTH_TOKEN")
    api_key_var = env.get("ANTHROPIC_API_KEY")

    if auth_token:
        return SettingsCredentials(
            api_key=auth_token,
            base_url=env.get("ANTHROPIC_BASE_URL"),
            model=env.get("ANTHROPIC_MODEL"),
            auth_scheme=AuthScheme.BEARER,
            is_present=True,
        )
    elif api_key_var:
        return SettingsCredentials(
            api_key=api_key_var,
            base_url=env.get("ANTHROPIC_BASE_URL"),
            model=env.get("ANTHROPIC_MODEL"),
            auth_scheme=AuthScheme.X_API_KEY,
            is_present=True,
        )
    else:
        return SettingsCredentials(
            api_key=None,
            base_url=None,
            model=None,
            auth_scheme=None,
            is_present=False,
        )
