"""Session persistence layer.

Public API:
    - SessionStore: JSONL session read/write with append + delta replay
    - DeltaIterator: Line-by-line JSONL reader with byte offset tracking
    - SessionMeta: Session metadata Pydantic model
    - FileLock: Cross-platform file locking (fcntl/msvcrt)
"""

from code_agent.storage.file_lock import FileLock
from code_agent.storage.models import SessionMeta
from code_agent.storage.session_store import DeltaIterator, SessionStore

__all__ = ["SessionStore", "DeltaIterator", "SessionMeta", "FileLock"]
