from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from agent_overlord.domain.events import WallEvent
from agent_overlord.domain.council import (
    ActionProposal,
    ControllerRuntimeState,
    CouncilNotification,
    ProposalCritique,
    ProposalVote,
)
from agent_overlord.domain.ignored import IgnoredSession
from agent_overlord.domain.memories import Memory, MemoryKind
from agent_overlord.domain.workers import Worker
from agent_overlord.domain.prompts import (
    ApprovalPolicy,
    AutomationSettings,
    MatchKind,
    PromptDecision,
    PromptRequest,
    PromptRisk,
    ReviewTier,
)


class HostHealth(BaseModel):
    name: str
    connected: bool
    error: str | None = None
    worker_count: int = 0


class HealthResponse(BaseModel):
    status: str
    inventory_running: bool
    started_at: datetime
    configured_hosts: int
    workers: int
    stream_clients: int
    hosts: list[HostHealth]


class WorkersResponse(BaseModel):
    workers: list[Worker]


class IgnoredSessionsResponse(BaseModel):
    ignored_sessions: list[IgnoredSession]


class IgnoreSessionResponse(BaseModel):
    ignored_session: IgnoredSession
    removed_worker_ids: list[str]


class RestoreIgnoredSessionsResponse(BaseModel):
    restored_ignore_ids: list[str]
    workers: list[Worker]


class EventsResponse(BaseModel):
    events: list[WallEvent]


class ChatMessage(BaseModel):
    role: str
    message: str


class ChatHistoryResponse(BaseModel):
    messages: list[ChatMessage]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)


class ChatResponse(BaseModel):
    message: str
    worker_ids: list[str]
    status: str = "completed"
    notification_id: str | None = None


class MemoriesResponse(BaseModel):
    memories: list[Memory]


class MemoryCreateRequest(BaseModel):
    claim: str = Field(min_length=1, max_length=20_000)
    scope: str = Field(default="global", min_length=1, max_length=500)
    kind: MemoryKind = MemoryKind.SEMANTIC


class MemoryUpdateRequest(BaseModel):
    claim: str = Field(min_length=1, max_length=20_000)


class RefreshResponse(BaseModel):
    accepted: bool = True


class PromptDecisionRequest(BaseModel):
    decision: PromptDecision
    choice: str | None = None
    expected_fingerprint: str = Field(min_length=64, max_length=64)
    expected_worker_id: str = Field(min_length=1, max_length=200)
    expected_pane_id: str = Field(min_length=1, max_length=200)
    rationale: str = Field(default="explicit human decision", max_length=2000)
    execute: bool = False


class ApprovalPolicyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    decision: PromptDecision
    match_kind: MatchKind = MatchKind.EXACT
    command_argv: list[str] = Field(min_length=1)
    allowed_choices: list[str] = Field(default_factory=lambda: ["allow"])
    harness: str | None = None
    host: str | None = None
    project: str | None = None
    worker_id: str | None = None
    session_id: str | None = None
    risk_ceiling: PromptRisk = PromptRisk.ROUTINE
    provenance: str = "explicit user policy"


class AutomationSettingsUpdateRequest(BaseModel):
    automation_enabled: bool | None = None
    dry_run: bool | None = None
    paused: bool | None = None
    disabled_hosts: list[str] | None = None
    disabled_projects: list[str] | None = None
    disabled_sessions: list[str] | None = None
    disabled_workers: list[str] | None = None
    auto_yes_workers: list[str] | None = None
    prompt_expiration_secs: float | None = Field(default=None, gt=0)
    verification_timeout_secs: float | None = Field(default=None, gt=0)
    max_actions_per_pane_per_hour: int | None = Field(default=None, ge=1)
    auto_yes_max_actions_per_worker_per_hour: int | None = Field(default=None, ge=1)
    review_precedent_ttl_secs: float | None = Field(default=None, gt=0)


class PromptReviewRequest(BaseModel):
    tier: ReviewTier


class Snapshot(BaseModel):
    workers: list[Worker]
    events: list[WallEvent]
    messages: list[ChatMessage]
    memories: list[Memory]
    health: HealthResponse
    controllers: list[ControllerRuntimeState] = Field(default_factory=list)
    notifications: list[CouncilNotification] = Field(default_factory=list)
    ignored_sessions: list[IgnoredSession] = Field(default_factory=list)
    prompts: list[PromptRequest] = Field(default_factory=list)
    policies: list[ApprovalPolicy] = Field(default_factory=list)
    automation: AutomationSettings = Field(default_factory=AutomationSettings)


class ProposalDetail(BaseModel):
    proposal: ActionProposal
    critiques: list[ProposalCritique]
    votes: list[ProposalVote]
