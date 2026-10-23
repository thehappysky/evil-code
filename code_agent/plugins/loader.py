# [Phase 4] 2026-07-29: Plugin system — importlib-based discovery and loading
"""Plugin system.

References:
    - design-docs/Python-Agent-MVP-Phase4-详细设计.md §4 plugins/
    - Rust plugin marketplace (xai-grok-plugin-marketplace)

Plugin directory structure::

    ~/.code-agent/plugins/{plugin_name}/
        plugin.toml       -- metadata (name, version, description, author)
        tools.py          -- extra tool functions (optional)
        skills/           -- extra Skills directory (optional)
        hooks.py          -- lifecycle hook handlers (optional)

Discovery order (project overrides user):
    1. Built-in plugins: bundled with code_agent package
    2. User plugins: ~/.code-agent/plugins/
    3. Project plugins: {cwd}/.code-agent/plugins/
"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path

from code_agent.plugins.models import PluginManifest

logger = logging.getLogger(__name__)


class PluginLoader:
    """Importlib-based plugin discovery and loading.

    Usage::

        loader = PluginLoader(plugins_dir=Path.home() / ".code-agent" / "plugins")
        manifests = loader.discover()
        for manifest in manifests:
            tools = loader.load_tools(manifest)
            hooks = loader.load_hooks(manifest)
    """

    def __init__(self, plugins_dir: Path | None = None):
        self._plugins_dir = plugins_dir or Path.home() / ".code-agent" / "plugins"

    # ── Discovery ────────────────────────────────────────────────────────

    def discover(self) -> list[PluginManifest]:
        """Scan plugin directories and return all valid plugin manifests.

        Invalid or malformed plugins are skipped with a warning.
        Project plugins override user plugins for same-name conflicts.

        Returns:
            List of valid PluginManifest objects.
        """
        manifests: list[PluginManifest] = []
        seen_names: set[str] = set()

        # Scan project directory first (higher priority)
        for source_dir in self._get_scan_dirs():
            if not source_dir.exists():
                continue

            for entry in sorted(source_dir.iterdir()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue

                toml_path = entry / "plugin.toml"
                if not toml_path.exists():
                    logger.debug(
                        "Skipping '%s': no plugin.toml found", entry.name,
                    )
                    continue

                try:
                    manifest = _load_manifest(toml_path)
                    manifest._plugin_dir = str(entry.resolve())

                    # Skip duplicates (first found wins)
                    if manifest.name in seen_names:
                        logger.debug(
                            "Plugin '%s' shadowed by earlier discovery",
                            manifest.name,
                        )
                        continue

                    seen_names.add(manifest.name)
                    manifests.append(manifest)

                except (ValueError, OSError) as exc:
                    logger.warning(
                        "Skipping invalid plugin at '%s': %s",
                        toml_path, exc,
                    )

        logger.info(
            "Discovered %d plugins from %d source(s)",
            len(manifests),
            len(self._get_scan_dirs()),
        )
        return manifests

    # ── Loading ──────────────────────────────────────────────────────────

    def load_tools(self, manifest: PluginManifest) -> list:
        """Load tool functions from plugin's tools.py module.

        Args:
            manifest: PluginManifest with a tools field populated.

        Returns:
            List of async callable tool handler functions.
        """
        tools: list = []
        plugin_dir = getattr(manifest, "_plugin_dir", None)
        if not plugin_dir:
            return tools

        tools_py = Path(plugin_dir) / "tools.py"
        if not tools_py.exists():
            return tools

        try:
            module = _import_module_from_file(
                f"plugin_{manifest.name}_tools",
                tools_py,
            )
            for name in dir(module):
                obj = getattr(module, name)
                if callable(obj) and not name.startswith("_"):
                    tools.append(obj)
            logger.debug(
                "Loaded %d tool(s) from plugin '%s'",
                len(tools), manifest.name,
            )
        except Exception as exc:
            logger.warning(
                "Failed to load tools from plugin '%s': %s",
                manifest.name, exc,
            )

        return tools

    def load_hooks(self, manifest: PluginManifest) -> list:
        """Load hook handlers from plugin's hooks.py module.

        Args:
            manifest: PluginManifest with a hooks field.

        Returns:
            List of async callable hook handler functions.
        """
        hooks: list = []
        plugin_dir = getattr(manifest, "_plugin_dir", None)
        if not plugin_dir:
            return hooks

        hooks_py = Path(plugin_dir) / "hooks.py"
        if not hooks_py.exists():
            return hooks

        try:
            module = _import_module_from_file(
                f"plugin_{manifest.name}_hooks",
                hooks_py,
            )
            for name in dir(module):
                obj = getattr(module, name)
                if callable(obj) and not name.startswith("_"):
                    hooks.append(obj)
            logger.debug(
                "Loaded %d hook(s) from plugin '%s'",
                len(hooks), manifest.name,
            )
        except Exception as exc:
            logger.warning(
                "Failed to load hooks from plugin '%s': %s",
                manifest.name, exc,
            )

        return hooks

    # ── Internal ─────────────────────────────────────────────────────────

    def _get_scan_dirs(self) -> list[Path]:
        """Get list of plugin directories to scan, in priority order."""
        return [self._plugins_dir]


def _load_manifest(toml_path: Path) -> PluginManifest:
    """Parse a plugin.toml file into a PluginManifest.

    Args:
        toml_path: Path to plugin.toml.

    Returns:
        PluginManifest with parsed fields.

    Raises:
        ValueError: If the toml is malformed or missing required fields.
    """
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))

    # Support both [plugin] section and flat TOML
    if "plugin" in data:
        data = data["plugin"]

    if "name" not in data:
        raise ValueError("plugin.toml missing required 'name' field")

    return PluginManifest(
        name=data["name"],
        version=str(data.get("version", "0.0.0")),
        description=data.get("description", ""),
        author=data.get("author", ""),
        tools=data.get("tools", []),
        skills=data.get("skills", []),
        hooks=data.get("hooks", []),
    )


def _import_module_from_file(module_name: str, filepath: Path):
    """Import a Python module from a file path.

    Args:
        module_name: Name for the module.
        filepath: Path to the .py file.

    Returns:
        Imported Python module.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(module_name, str(filepath))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {filepath}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
