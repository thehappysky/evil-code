# [Phase 4] 2026-07-29: Tests for plugins/loader.py
"""Tests for PluginLoader."""

import tempfile
from pathlib import Path

import pytest

from code_agent.plugins.loader import PluginLoader, _load_manifest


class TestPluginLoader:
    """Test PluginLoader discovery and loading."""

    def test_discover_empty_dir(self):
        """Empty plugin dir returns empty list."""
        with tempfile.TemporaryDirectory() as tmp:
            loader = PluginLoader(plugins_dir=Path(tmp))
            manifests = loader.discover()
            assert manifests == []

    def test_discover_valid_plugin(self):
        """Valid plugin with plugin.toml is discovered."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "my-plugin"
            plugin_dir.mkdir()
            (plugin_dir / "plugin.toml").write_text(
                '[plugin]\nname = "my-plugin"\nversion = "1.0.0"\n',
                encoding="utf-8",
            )

            loader = PluginLoader(plugins_dir=Path(tmp))
            manifests = loader.discover()

            assert len(manifests) == 1
            assert manifests[0].name == "my-plugin"
            assert manifests[0].version == "1.0.0"

    def test_discover_skips_invalid_plugin(self):
        """Directory without plugin.toml is skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "bad-plugin"
            plugin_dir.mkdir()
            # No plugin.toml

            loader = PluginLoader(plugins_dir=Path(tmp))
            manifests = loader.discover()
            assert manifests == []

    def test_discover_skips_hidden_dirs(self):
        """Hidden directories (.name) are skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / ".hidden-plugin"
            plugin_dir.mkdir()
            (plugin_dir / "plugin.toml").write_text(
                '[plugin]\nname = "hidden"\nversion = "1.0"\n',
                encoding="utf-8",
            )

            loader = PluginLoader(plugins_dir=Path(tmp))
            manifests = loader.discover()
            assert manifests == []

    def test_load_tools_empty_when_no_tools_py(self):
        """load_tools returns empty when no tools.py."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "simple-plugin"
            plugin_dir.mkdir()
            (plugin_dir / "plugin.toml").write_text(
                '[plugin]\nname = "simple"\nversion = "1.0"\n',
                encoding="utf-8",
            )

            loader = PluginLoader(plugins_dir=Path(tmp))
            manifests = loader.discover()

            assert len(manifests) == 1
            tools = loader.load_tools(manifests[0])
            assert tools == []


class TestLoadManifest:
    """Test _load_manifest function."""

    def test_valid_toml(self):
        """Valid TOML is parsed into PluginManifest."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toml", delete=False, encoding="utf-8",
        ) as f:
            f.write(
                '[plugin]\n'
                'name = "test"\n'
                'version = "2.0.0"\n'
                'description = "A test plugin"\n'
                'author = "Dev"\n'
            )
            path = Path(f.name)

        try:
            manifest = _load_manifest(path)
            assert manifest.name == "test"
            assert manifest.version == "2.0.0"
            assert manifest.description == "A test plugin"
            assert manifest.author == "Dev"
        finally:
            path.unlink(missing_ok=True)

    def test_missing_name_raises(self):
        """Missing name raises ValueError."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toml", delete=False, encoding="utf-8",
        ) as f:
            f.write('[plugin]\nversion = "1.0"\n')
            path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="name"):
                _load_manifest(path)
        finally:
            path.unlink(missing_ok=True)
