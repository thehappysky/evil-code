"""Cross-session memory layer.

Public API:
    - MemoryIndex: SQLite + FTS5 memory chunk index
    - MemoryStore: MEMORY.md file I/O + session log management
    - search_memory: Hybrid search pipeline (FTS5 -> rank -> filter)
    - chunk_markdown: Split Markdown text by H2 headers
    - Chunk: Chunk dataclass
    - SearchResult: Search result dataclass
"""

from code_agent.memory.chunker import Chunk, chunk_markdown
from code_agent.memory.file_store import MemoryStore
from code_agent.memory.index_db import MemoryIndex
from code_agent.memory.search import SearchResult, search_memory

__all__ = [
    "MemoryIndex",
    "MemoryStore",
    "search_memory",
    "chunk_markdown",
    "Chunk",
    "SearchResult",
]
