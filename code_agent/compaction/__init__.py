"""Dialog compression layer.

Public API:
    - CompactionPolicy: Token threshold trigger strategy
    - generate_summary: LLM-based conversation summarization
    - assemble_compacted_history: Post-compaction history assembly
"""

from code_agent.compaction.assembler import assemble_compacted_history
from code_agent.compaction.policy import CompactionPolicy
from code_agent.compaction.summarizer import classify_compact_error, generate_summary

__all__ = [
    "CompactionPolicy",
    "generate_summary",
    "assemble_compacted_history",
    "classify_compact_error",
]
