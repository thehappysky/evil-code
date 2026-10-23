"""Session metadata model.

References: Rust SessionMeta equivalent in storage layer.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class SessionMeta(BaseModel):
    """Metadata for a single Agent session.

    Stored as summary.json alongside chat_history.jsonl.
    Used by SessionStore.list_sessions() and find_latest() for session discovery.
    """

    session_id: str
    cwd: str
    model_id: str
    title: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    prompt_count: int = 0
    total_tokens: int = 0

    def touch(self) -> None:
        """Update the updated_at timestamp to now."""
        self.updated_at = datetime.now(timezone.utc)
