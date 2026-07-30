from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class EventKind(StrEnum):
    SYSTEM = "system"
    DISCOVERED = "discovered"
    STATE_CHANGED = "state_changed"
    PURPOSE_CHANGED = "purpose_changed"
    INPUT_REQUESTED = "input_requested"
    DISCONNECTED = "disconnected"
    RECOVERED = "recovered"
    HUMAN_MESSAGE = "human_message"
    COUNCIL_MESSAGE = "council_message"
    MEMORY = "memory"
    WARNING = "warning"
    ERROR = "error"
    INTERPRETATION = "interpretation"
    CONTROLLER_MESSAGE = "controller_message"
    CONTROLLER_LIFECYCLE = "controller_lifecycle"
    PROPOSAL = "proposal"
    NOTIFICATION = "notification"
    PROMPT = "prompt"
    POLICY = "policy"
    ACTION = "action"
    CONTROLLER_LOG = "controller_log"


class WallEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: str
    kind: EventKind
    message: str
    worker_id: str | None = None
    host: str | None = None
    intent: str | None = None
    severity: str = "info"
    data: dict[str, Any] = Field(default_factory=dict)
