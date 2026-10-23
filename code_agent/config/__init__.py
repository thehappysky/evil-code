"""Config package — generate and manage evil-code config files.

Two config paths (in priority order):
1. ~/.evil-code/config.yaml   — evil-code native (primary, YAML)
2. ~/.claude/settings.json     — cc-switch compatible (fallback, JSON)

The "parameter → config file" flow writes to the native config file.
settings.json is read as a backward-compatible fallback via auth/config_store.py.
"""

from code_agent.auth.config_store import generate_config, load_config, save_config
from code_agent.config.settings_writer import (
    clear_settings_config,
    save_settings_config,
    show_settings_config,
)

__all__ = [
    "generate_config",
    "load_config",
    "save_config",
    "save_settings_config",
    "show_settings_config",
    "clear_settings_config",
]
