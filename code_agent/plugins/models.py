# [Phase 4] 2026-07-29: Plugin manifest model
"""PluginManifest Pydantic model.

References:
    - design-docs/Python-Agent-MVP-Phase4-详细设计.md §4 plugins/models.py
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PluginToolArgs(BaseModel):
    """Permissive input model for plugin-contributed tools.

    Plugins declare tool functions as plain async/sync callables with keyword
    arguments (e.g. ``async def my_tool(query: str) -> str``) and no Pydantic
    schema. This model accepts any extra keyword arguments, so the callable's
    parameters are passed through as ``**kwargs``.

    Plugins that need a strict schema should define their own ``BaseModel``
    in ``tools.py`` and attach it as ``__input_model__`` on the callable —
    the registry honours that when present (see PluginLoader note).
    """

    model_config = ConfigDict(extra="allow")


class PluginManifest(BaseModel):
    """Plugin metadata parsed from plugin.toml.

    Attributes:
        name: Plugin identifier.
        version: Semantic version string.
        description: Human-readable description.
        author: Plugin author.
        tools: Module names providing tool functions.
        skills: Directory names containing SKILL.md files.
        hooks: Module names providing hook handlers.
    """

    name: str = Field(description="Plugin identifier")
    version: str = Field(default="0.0.0", description="Semantic version")
    description: str = Field(default="", description="Human-readable description")
    author: str = Field(default="", description="Plugin author")
    tools: list[str] = Field(
        default_factory=list,
        description="Module names providing tool functions",
    )
    skills: list[str] = Field(
        default_factory=list,
        description="Directory names containing SKILL.md files",
    )
    hooks: list[str] = Field(
        default_factory=list,
        description="Module names providing hook handlers",
    )

    # Internal: set at runtime by PluginLoader
    _plugin_dir: str = ""
