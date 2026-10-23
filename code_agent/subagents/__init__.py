# [Phase 7] 2026-08-04: Subagents package
"""Sub-agent orchestration (multi-agent work delegation).

Public surface: SubagentCoordinator + the task tool (tools/builtin/task.py).
See design-docs/Python-Agent-补足方案-设计.md §Phase 7.
"""

from code_agent.subagents.capabilities import capability_tool_names
from code_agent.subagents.coordinator import SubagentCoordinator
from code_agent.subagents.factory import build_subagent_factory

__all__ = ["SubagentCoordinator", "build_subagent_factory", "capability_tool_names"]
