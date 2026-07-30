from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class MemoryKind(StrEnum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    PREFERENCE = "preference"


class MemoryStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    STALE = "stale"


class Memory(BaseModel):
    memory_id: str = Field(default_factory=lambda: uuid4().hex)
    scope: str = "global"
    kind: MemoryKind = MemoryKind.SEMANTIC
    claim: str
    source: str = "user"
    created_by: str = "user"
    confidence: float = 1.0
    status: MemoryStatus = MemoryStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
