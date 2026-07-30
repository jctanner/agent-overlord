from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class PromptStatus(StrEnum):
    DETECTED = "detected"
    EVALUATING = "evaluating"
    ESCALATED = "escalated"
    DECIDED = "decided"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    STALE = "stale"
    FAILED = "failed"
    EXPIRED = "expired"


class PromptRisk(StrEnum):
    ROUTINE = "routine"
    ELEVATED = "elevated"
    HIGH = "high"
    UNKNOWN = "unknown"


class ReviewTier(StrEnum):
    POLICY = "policy"
    FAST = "fast"
    COUNCIL = "council"
    HUMAN = "human"


class PromptDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"


class PolicyStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    EXPIRED = "expired"


class MatchKind(StrEnum):
    EXACT = "exact"
    ARGV_PREFIX = "argv_prefix"


class PromptChoice(BaseModel):
    choice: str
    label: str
    response: str


class PromptRequest(BaseModel):
    prompt_id: str = Field(default_factory=lambda: uuid4().hex)
    worker_id: str
    host: str
    tmux_socket: str = "default"
    session_id: str
    session_name: str
    window_id: str
    window_name: str
    pane_id: str
    pane_index: int = 0
    harness: str
    project: str | None = None
    prompt_type: str
    operation: str
    normalized_argv: list[str] = Field(default_factory=list)
    choices: list[PromptChoice] = Field(default_factory=list)
    observation_fingerprint: str
    prompt_signature: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    risk: PromptRisk = PromptRisk.UNKNOWN
    risk_reasons: list[str] = Field(default_factory=list)
    tier: ReviewTier = ReviewTier.HUMAN
    status: PromptStatus = PromptStatus.DETECTED
    decision: PromptDecision | None = None
    selected_choice: str | None = None
    decision_source: str | None = None
    policy_id: str | None = None
    reviewer_ids: list[str] = Field(default_factory=list)
    review_notification_id: str | None = None
    review_decisions: dict[str, PromptDecision] = Field(default_factory=dict)
    review_choices: dict[str, str] = Field(default_factory=dict)
    review_rationales: dict[str, str] = Field(default_factory=dict)
    rationale: str | None = None
    pre_action_fingerprint: str | None = None
    post_action_fingerprint: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None
    executed_at: datetime | None = None
    completed_at: datetime | None = None


class ApprovalPolicy(BaseModel):
    policy_id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    status: PolicyStatus = PolicyStatus.ACTIVE
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
    created_by: str = "user"
    provenance: str = "explicit user policy"
    require_post_verification: bool = True
    usage_count: int = 0
    failure_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    confirmed_at: datetime | None = None
    revoked_at: datetime | None = None


class AutomationSettings(BaseModel):
    automation_enabled: bool = False
    dry_run: bool = True
    paused: bool = False
    disabled_hosts: list[str] = Field(default_factory=list)
    disabled_projects: list[str] = Field(default_factory=list)
    disabled_sessions: list[str] = Field(default_factory=list)
    disabled_workers: list[str] = Field(default_factory=list)
    # Worker ids identify one host/socket/session/window/pane row. Auto yes is
    # intentionally pane-scoped so sibling panes in one tmux session are not
    # granted authority by the same click.
    auto_yes_workers: list[str] = Field(default_factory=list)
    prompt_expiration_secs: float = Field(default=120.0, gt=0)
    verification_timeout_secs: float = Field(default=8.0, gt=0)
    max_actions_per_pane_per_hour: int = Field(default=20, ge=1)
    auto_yes_max_actions_per_worker_per_hour: int = Field(default=100, ge=1)
    review_precedent_ttl_secs: float = Field(default=604800.0, gt=0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
