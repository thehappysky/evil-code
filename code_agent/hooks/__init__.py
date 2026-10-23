"""AgentHook implementations for Phase 2-4.

Phase 2 hooks:
    - MemoryHook: Inject cross-session memory at session start
    - CompactionHook: Trigger auto-compaction when token threshold exceeded
    - PersistenceHook: Save session to disk after each turn
    - EditTrackerHook: Track files edited by the agent
"""

from code_agent.hooks.phase2_hooks import (
    CompactionHook,
    EditTrackerHook,
    MemoryHook,
    PersistenceHook,
)

__all__ = [
    "MemoryHook",
    "CompactionHook",
    "PersistenceHook",
    "EditTrackerHook",
]
