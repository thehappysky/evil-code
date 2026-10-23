"""Shared runtime construction and resource ownership, independent of UI."""

from code_agent.runtime.session import AgentRuntime, close_resource, open_runtime, stop_background_task

__all__ = ["AgentRuntime", "close_resource", "open_runtime", "stop_background_task"]
