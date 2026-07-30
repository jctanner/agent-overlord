from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, model_validator

from agent_overlord.domain.council import ControllerRole
from agent_overlord.domain.prompts import AutomationSettings


class HostConfig(BaseModel):
    name: str
    local: bool = False
    ssh: str | None = None
    port: int = 22
    key: str | None = None
    tmux_socket: str = "default"

    @model_validator(mode="after")
    def validate_transport(self) -> "HostConfig":
        if not self.local and not self.ssh:
            raise ValueError(f"host {self.name!r} requires local: true or ssh")
        return self


class ControllerConfig(BaseModel):
    controller_id: str
    role: ControllerRole
    harness: str
    model: str
    enabled: bool = True
    turn_timeout_secs: float = Field(default=300, gt=0)
    max_turns: int = Field(default=30, ge=1, le=200)
    environment: dict[str, str] = Field(default_factory=dict)


class AppConfig(BaseModel):
    hosts: list[HostConfig]
    poll_interval_secs: float = Field(default=2.0, gt=0)
    attention_patterns: list[str] = Field(
        default_factory=lambda: [
            "Do you want to",
            "Allow",
            "yes/no",
            "Permission",
            "Press enter",
        ]
    )
    stalled_after_secs: float = Field(default=900.0, gt=0)
    capture_lines: int = Field(default=80, ge=15, le=2000)
    disappearance_confirmations: int = Field(default=3, ge=1, le=20)
    controllers: list[ControllerConfig] = Field(default_factory=list)
    controller_runtime_enabled: bool = False
    controller_image: str = "localhost/agent-overlord-controller:latest"
    controller_mcp_url: str = "http://127.0.0.1:8001"
    controller_restart_limit: int = Field(default=3, ge=0, le=20)
    notification_retry_limit: int = Field(default=2, ge=0, le=10)
    notification_retry_delay_secs: float = Field(default=2.0, ge=0, le=300)
    worker_analysis_cooldown_secs: float = Field(default=900.0, ge=0, le=86_400)
    fast_reviewer_controller_id: str | None = None
    fast_review_timeout_secs: float = Field(default=60.0, gt=0)
    automation: AutomationSettings = Field(default_factory=AutomationSettings)

    @model_validator(mode="after")
    def validate_controller_gateway(self) -> "AppConfig":
        if self.controller_runtime_enabled:
            parsed = urlparse(self.controller_mcp_url)
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
                raise ValueError(
                    "controller_mcp_url must be an HTTP loopback URL; Podman exposes "
                    "only that port to controller containers"
                )
            if parsed.port is None:
                raise ValueError("controller_mcp_url requires an explicit port")
        return self

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        with Path(path).open(encoding="utf-8") as stream:
            return cls.model_validate(yaml.safe_load(stream) or {})
