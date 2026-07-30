from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class IgnoredSession(BaseModel):
    ignore_id: str = Field(default_factory=lambda: uuid4().hex)
    host: str
    tmux_socket: str
    session_id: str
    session_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def matches(self, host: str, tmux_socket: str, session_id: str) -> bool:
        return (
            self.host == host
            and self.tmux_socket == tmux_socket
            and self.session_id == session_id
        )
