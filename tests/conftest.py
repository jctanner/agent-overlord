from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_overlord.config import AppConfig, HostConfig
from agent_overlord.domain.workers import PaneObservation


@pytest.fixture
def config() -> AppConfig:
    return AppConfig(
        hosts=[HostConfig(name="local", local=True)],
        poll_interval_secs=0.05,
        stalled_after_secs=60,
    )


@pytest.fixture
def codex_observation() -> PaneObservation:
    return PaneObservation(
        host="local",
        session_id="$1",
        session_name="project",
        window_id="@2",
        window_name="agent",
        pane_id="%3",
        pane_index=0,
        pane_title="codex",
        current_path="/work/example",
        current_command="codex",
        start_command="codex --model gpt-5",
        pane_pid=1234,
        content=[
            "Implementing the requested change",
            "Allow command `uv run pytest`? (y/n)",
        ],
        observed_at=datetime.now(UTC),
    )

