from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class ControllerRole(StrEnum):
    OPERATOR = "operator"
    AUDITOR = "auditor"
    STRATEGIST = "strategist"


class ControllerStatus(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    BUSY = "busy"
    FAILED = "failed"
    RESTARTING = "restarting"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    SUPERSEDED = "superseded"


class ProposalStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    STALE = "stale"


class VoteValue(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    ABSTAIN = "abstain"


class EvidenceReference(BaseModel):
    kind: str
    reference: str
    excerpt: str = ""


class SemanticInterpretation(BaseModel):
    interpretation_id: str = Field(default_factory=lambda: uuid4().hex)
    worker_id: str
    observation_fingerprint: str
    goal: str | None = None
    current_activity: str | None = None
    blocker: str | None = None
    requested_operation: str | None = None
    project_context: str | None = None
    completion_criteria: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    controller_id: str
    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CouncilNotification(BaseModel):
    notification_id: str = Field(default_factory=lambda: uuid4().hex)
    reason: str
    priority: int = Field(default=50, ge=0, le=100)
    target_roles: list[ControllerRole] = Field(default_factory=list)
    target_controller_ids: list[str] = Field(default_factory=list)
    prompt_id: str | None = None
    worker_id: str | None = None
    observation_fingerprint: str | None = None
    human_message: str | None = None
    status: NotificationStatus = NotificationStatus.PENDING
    attempts: int = 0
    summary: str | None = None
    answer: str | None = None
    answer_references: list[str] = Field(default_factory=list)
    answered_by: str | None = None
    answer_published_at: datetime | None = None
    completion_signals: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ActionProposal(BaseModel):
    proposal_id: str = Field(default_factory=lambda: uuid4().hex)
    controller_id: str
    operation: str
    target_worker_id: str | None = None
    observation_fingerprint: str | None = None
    rationale: str
    risk: str = "unknown"
    status: ProposalStatus = ProposalStatus.OPEN
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProposalCritique(BaseModel):
    critique_id: str = Field(default_factory=lambda: uuid4().hex)
    proposal_id: str
    controller_id: str
    findings: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProposalVote(BaseModel):
    vote_id: str = Field(default_factory=lambda: uuid4().hex)
    proposal_id: str
    controller_id: str
    vote: VoteValue
    rationale: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ControllerRuntimeState(BaseModel):
    controller_id: str
    role: ControllerRole
    harness: str
    model: str
    status: ControllerStatus = ControllerStatus.STOPPED
    container_name: str | None = None
    session_id: str | None = None
    current_notification_id: str | None = None
    cycles_completed: int = 0
    restart_count: int = 0
    last_duration_secs: float | None = None
    last_usage: dict = Field(default_factory=dict)
    last_error: str | None = None
    last_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
