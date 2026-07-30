from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_overlord.config import AppConfig, ControllerConfig, HostConfig
from agent_overlord.domain.council import ControllerRole, NotificationStatus
from agent_overlord.domain.council import CouncilNotification
from agent_overlord.domain.workers import PaneObservation, Worker, WorkerState
from agent_overlord.services.controller_backends import ControllerTurnOutput
from agent_overlord.services.controller_runtime import ControllerTurnTimeout
from agent_overlord.services.council_scheduler import CouncilScheduler
from agent_overlord.services.inventory import InventoryService
from agent_overlord.storage.sqlite import SQLiteStore


def config(**overrides) -> AppConfig:
    controllers = [
        ControllerConfig(controller_id="operator", role="operator", harness="claude.vertex", model="sonnet"),
        ControllerConfig(controller_id="auditor", role="auditor", harness="codex", model="gpt-5"),
        ControllerConfig(controller_id="strategist", role="strategist", harness="claude.vertex", model="opus"),
    ]
    return AppConfig(
        hosts=[HostConfig(name="local", local=True)], controllers=controllers,
        controller_runtime_enabled=True, notification_retry_delay_secs=0,
        **overrides,
    )


async def wait_terminal(store: SQLiteStore, notification_id: str):
    for _ in range(100):
        item = await store.get_notification(notification_id)
        if item and item.status in {
            NotificationStatus.COMPLETED, NotificationStatus.FAILED,
            NotificationStatus.TIMED_OUT,
            NotificationStatus.SUPERSEDED,
        }:
            return item
        await asyncio.sleep(0.01)
    raise AssertionError("notification did not finish")


@pytest.mark.asyncio
async def test_stale_worker_state_notification_is_superseded_before_controller_runs(
    tmp_path: Path,
) -> None:
    cfg = config(notification_retry_limit=0)
    store = SQLiteStore(tmp_path / "stale-worker-notification.db")
    await store.initialize()
    inventory = InventoryService(cfg, store)
    await inventory.initialize()
    observation = PaneObservation(
        host="local", session_id="$1", session_name="work", window_id="@1",
        window_name="agent", pane_id="%1", current_command="claude",
        content=["Thinking about the task"],
    )
    worker = Worker(
        worker_id=observation.worker_id, observation=observation,
        state=WorkerState.IDLE,
    )
    stale = CouncilNotification(
        reason="worker_idle", target_roles=[ControllerRole.OPERATOR],
        worker_id=worker.worker_id,
        observation_fingerprint=worker.observation.content_fingerprint,
    )
    await store.save_notification(stale)

    worker.state = WorkerState.AWAITING_INPUT
    worker.awaiting_input = True
    worker.observation.content = ["Do you want to proceed?", "1. Yes", "2. No"]
    inventory.workers[worker.worker_id] = worker
    pool = FakePool(store)
    scheduler = CouncilScheduler(
        cfg, store, inventory, pool, lambda *_: asyncio.sleep(0)
    )
    await scheduler.start()
    try:
        result = await wait_terminal(store, stale.notification_id)
        assert result.status == NotificationStatus.SUPERSEDED
        assert result.summary == "worker state changed before investigation began"
        assert pool.prompts == []
    finally:
        await scheduler.stop()


class FakePool:
    def __init__(
        self,
        store: SQLiteStore,
        error: Exception | None = None,
        published: list[tuple[str, str]] | None = None,
    ) -> None:
        self.prompts: list[tuple[str, str]] = []
        self.store = store
        self.error = error
        self.published = published

    async def run_turn(
        self, controller_id: str, prompt: str, *, notification_id: str | None = None,
        timeout_secs: float | None = None,
    ) -> ControllerTurnOutput:
        self.prompts.append((controller_id, prompt))
        assert notification_id
        if self.error:
            raise self.error
        if self.published is not None:
            assert self.published == []
        notification = await self.store.get_notification(notification_id)
        assert notification
        if controller_id == "strategist" and notification.human_message:
            notification.answer = "reviewed strategist answer"
            notification.answered_by = "strategist"
        notification.completion_signals[controller_id] = "done"
        await self.store.save_notification(notification)
        return ControllerTurnOutput(response_text=f"{controller_id} evidence-backed answer")


@pytest.mark.asyncio
async def test_human_question_runs_role_wave_with_targeted_prompt_and_one_answer(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "scheduler.db")
    await store.initialize()
    inventory = InventoryService(config(), store)
    await inventory.initialize()
    published: list[tuple[str, str]] = []
    pool = FakePool(store, published=published)

    async def publish(role: str, message: str) -> None:
        published.append((role, message))

    scheduler = CouncilScheduler(config(), store, inventory, pool, publish)
    await scheduler.start()
    try:
        item = await scheduler.enqueue_human_question("What is the worker's goal?")
        result = await wait_terminal(store, item.notification_id)
        assert result.status == NotificationStatus.COMPLETED
        assert result.answer == "reviewed strategist answer"
        assert result.answered_by == "strategist"
        assert result.answer_published_at is not None
        assert [controller for controller, _ in pool.prompts] == [
            "operator", "auditor", "strategist"
        ]
        assert all("Use MCP to retrieve current state" in prompt for _, prompt in pool.prompts)
        assert all("full fleet" not in prompt.lower() for _, prompt in pool.prompts)
        review_prompt = scheduler._turn_prompt(
            CouncilNotification(
                reason="prompt_review_council", prompt_id="prompt-one",
                worker_id="worker-one", observation_fingerprint="fingerprint",
            ),
            ControllerRole.OPERATOR,
        )
        assert "Always select one exact executable prompt choice" in review_prompt
        assert "never grants authorization" in review_prompt
        assert "path and SHA-256" in review_prompt
        assert published == [("council", "reviewed strategist answer")]
        assert await store.list_chat_messages() == [
            ("council", "reviewed strategist answer")
        ]
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_timeout_is_terminal_and_prompt_changes_bypass_general_queue(tmp_path: Path) -> None:
    cfg = config(notification_retry_limit=0)
    store = SQLiteStore(tmp_path / "timeout.db")
    await store.initialize()
    inventory = InventoryService(cfg, store)
    await inventory.initialize()
    scheduler = CouncilScheduler(
        cfg, store, inventory, FakePool(store, ControllerTurnTimeout("deadline")),
        lambda *_: asyncio.sleep(0),
    )
    await scheduler.start()
    try:
        item = await scheduler.enqueue_human_question("Explain this")
        result = await wait_terminal(store, item.notification_id)
        assert result.status == NotificationStatus.TIMED_OUT

        observation = PaneObservation(
            host="local", session_id="$1", session_name="work", window_id="@1",
            window_name="agent", pane_id="%1", current_command="codex", content=["first"],
        )
        worker = Worker(
            worker_id=observation.worker_id, observation=observation,
            state=WorkerState.AWAITING_INPUT, awaiting_input=True,
        )
        await scheduler.observe_workers([worker])
        worker.observation.content = ["changed", "Allow command?"]
        await scheduler.observe_workers([worker])
        await scheduler.observe_workers([worker])
        worker_items = [
            notice for notice in await store.list_notifications(limit=100)
            if notice.worker_id == worker.worker_id
        ]
        assert worker_items == []
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_startup_requeues_an_interrupted_notification(tmp_path: Path) -> None:
    cfg = config(notification_retry_limit=0)
    store = SQLiteStore(tmp_path / "recovery.db")
    await store.initialize()
    inventory = InventoryService(cfg, store)
    await inventory.initialize()
    from agent_overlord.domain.council import CouncilNotification

    item = CouncilNotification(
        reason="worker_active",
        status=NotificationStatus.RUNNING,
        target_roles=[ControllerRole.OPERATOR],
    )
    await store.save_notification(item)
    scheduler = CouncilScheduler(cfg, store, inventory, FakePool(store), lambda *_: asyncio.sleep(0))
    await scheduler.start()
    try:
        result = await wait_terminal(store, item.notification_id)
        assert result.status == NotificationStatus.COMPLETED
        assert result.attempts == 1
    finally:
        await scheduler.stop()
