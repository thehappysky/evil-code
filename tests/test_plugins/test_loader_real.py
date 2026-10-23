"""Real-loading tests for plugins/loader.py (load_tools/load_hooks paths)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from code_agent.plugins.loader import PluginLoader, _load_manifest


def _write_plugin(root: Path, name: str, *, tools: str = "", hooks: str = "") -> Path:
    """Create a plugin directory under root with plugin.toml + optional .py files."""
    pdir = root / name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "plugin.toml").write_text(
        f'name = "{name}"\nversion = "1.0.0"\ndescription = "test"\n',
        encoding="utf-8",
    )
    if tools:
        (pdir / "tools.py").write_text(tools, encoding="utf-8")
    if hooks:
        (pdir / "hooks.py").write_text(hooks, encoding="utf-8")
    return pdir


class TestDiscover:
    def test_discovers_valid_plugins(self, tmp_path):
        _write_plugin(tmp_path, "plugin_a")
        _write_plugin(tmp_path, "plugin_b")
        loader = PluginLoader(plugins_dir=tmp_path)
        manifests = loader.discover()
        names = {m.name for m in manifests}
        assert names == {"plugin_a", "plugin_b"}
        # _plugin_dir is populated.
        assert all(getattr(m, "_plugin_dir", None) for m in manifests)

    def test_skips_entries_without_plugin_toml(self, tmp_path):
        (tmp_path / "not_a_plugin").mkdir()
        (tmp_path / "not_a_plugin" / "random.txt").write_text("x", encoding="utf-8")
        _write_plugin(tmp_path, "real")
        loader = PluginLoader(plugins_dir=tmp_path)
        assert {m.name for m in loader.discover()} == {"real"}

    def test_skips_hidden_dirs(self, tmp_path):
        _write_plugin(tmp_path, ".hidden")
        loader = PluginLoader(plugins_dir=tmp_path)
        assert loader.discover() == []

    def test_missing_name_field_raises_value_error(self, tmp_path):
        pdir = tmp_path / "bad"
        pdir.mkdir()
        (pdir / "plugin.toml").write_text('version = "1"\n', encoding="utf-8")
        with pytest.raises(ValueError):
            _load_manifest(pdir / "plugin.toml")

    def test_missing_plugins_dir_is_empty(self, tmp_path):
        loader = PluginLoader(plugins_dir=tmp_path / "nonexistent")
        assert loader.discover() == []


class TestLoadTools:
    def test_loads_callables_from_tools_py(self, tmp_path):
        _write_plugin(
            tmp_path, "with_tools",
            tools="async def my_tool(x: int):\n    return x * 2\n\n"
                  "_private = 1\nasync def other():\n    return 1\n",
        )
        loader = PluginLoader(plugins_dir=tmp_path)
        manifest = loader.discover()[0]
        tools = loader.load_tools(manifest)
        names = {t.__name__ for t in tools}
        assert names == {"my_tool", "other"}

    def test_no_tools_py_returns_empty(self, tmp_path):
        _write_plugin(tmp_path, "bare")
        loader = PluginLoader(plugins_dir=tmp_path)
        assert loader.load_tools(loader.discover()[0]) == []

    def test_broken_tools_py_degrades_to_empty(self, tmp_path):
        _write_plugin(tmp_path, "broken", tools="def syntax error(")
        loader = PluginLoader(plugins_dir=tmp_path)
        assert loader.load_tools(loader.discover()[0]) == []

    def test_missing_plugin_dir_returns_empty(self, tmp_path):
        from code_agent.plugins.models import PluginManifest

        loader = PluginLoader(plugins_dir=tmp_path)
        assert loader.load_tools(PluginManifest(name="x")) == []


class TestLoadHooks:
    def test_loads_hooks_py(self, tmp_path):
        _write_plugin(
            tmp_path, "with_hooks",
            hooks="async def on_turn(session, turn):\n    return None\n",
        )
        loader = PluginLoader(plugins_dir=tmp_path)
        manifest = loader.discover()[0]
        hooks = loader.load_hooks(manifest)
        assert {h.__name__ for h in hooks} == {"on_turn"}

    def test_no_hooks_py_returns_empty(self, tmp_path):
        _write_plugin(tmp_path, "bare")
        loader = PluginLoader(plugins_dir=tmp_path)
        assert loader.load_hooks(loader.discover()[0]) == []


class TestModuleIsolation:
    def test_imported_modules_do_not_accumulate(self, tmp_path):
        # Each plugin's tools.py gets a distinct module name, so loading the
        # same plugin twice must not clobber sys.modules entries of others.
        _write_plugin(tmp_path, "p1", tools="async def a():\n    return 1\n")
        _write_plugin(tmp_path, "p2", tools="async def b():\n    return 2\n")
        loader = PluginLoader(plugins_dir=tmp_path)
        for m in loader.discover():
            loader.load_tools(m)
        assert "plugin_p1_tools" in sys.modules
        assert "plugin_p2_tools" in sys.modules
