from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256

from pydantic import BaseModel, Field, computed_field


class WorkerState(StrEnum):
    ACTIVE = "active"
    AWAITING_INPUT = "awaiting_input"
    IDLE = "idle"
    STALLED = "stalled"
    FAILED = "failed"
    COMPLETE = "complete"
    DISCONNECTED = "disconnected"
    UNKNOWN = "unknown"


class InputKind(StrEnum):
    PERMISSION = "permission"
    CLARIFICATION = "clarification"
    SELECTION = "selection"
    CREDENTIAL = "credential"
    CONFIRMATION = "confirmation"
    UNKNOWN = "unknown"


class PaneObservation(BaseModel):
    host: str
    tmux_socket: str = "default"
    session_id: str
    session_name: str
    window_id: str
    window_index: int = 0
    window_name: str
    pane_id: str
    pane_index: int = 0
    pane_title: str = ""
    current_path: str = ""
    current_command: str = ""
    start_command: str = ""
    pane_pid: int | None = None
    descendant_commands: list[str] = Field(default_factory=list)
    pane_dead: bool = False
    pane_active: bool = False
    pane_activity: int | None = None
    width: int | None = None
    height: int | None = None
    content: list[str] = Field(default_factory=list)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @computed_field
    @property
    def worker_id(self) -> str:
        value = ":".join(
            (self.host, self.tmux_socket, self.session_id, self.window_id, self.pane_id)
        )
        return sha256(value.encode()).hexdigest()[:16]

    @computed_field
    @property
    def display_name(self) -> str:
        return f"{self.session_name}:{self.window_name}.{self.pane_index}"

    @computed_field
    @property
    def content_fingerprint(self) -> str:
        return sha256("\n".join(self.content).encode(errors="replace")).hexdigest()


class Worker(BaseModel):
    worker_id: str
    observation: PaneObservation
    harness: str = "unknown"
    model: str = "unknown"
    context: str = "unknown"
    purpose: str = "Unknown"
    project: str | None = None
    state: WorkerState = WorkerState.UNKNOWN
    awaiting_input: bool = False
    input_kind: InputKind | None = None
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    unchanged_since: datetime = Field(default_factory=lambda: datetime.now(UTC))
