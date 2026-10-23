# [Phase 4] 2026-07-29: Plugin system package
"""Plugin system package."""

from code_agent.plugins.loader import PluginLoader
from code_agent.plugins.models import PluginManifest
from code_agent.plugins.registry import hook_event_name_for, register_plugin_hook

__all__ = [
    "PluginLoader",
    "PluginManifest",
    "hook_event_name_for",
    "register_plugin_hook",
]
