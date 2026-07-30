from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime

from agent_overlord.api.models import (
    ChatMessage,
    HealthResponse,
    HostHealth,
    Snapshot,
)
from agent_overlord.config import AppConfig
from agent_overlord.domain.events import WallEvent
from agent_overlord.domain.events import EventKind
from agent_overlord.domain.memories import Memory
from agent_overlord.domain.workers import Worker
from agent_overlord.services.broadcast import EventBroadcaster
from agent_overlord.services.council import CouncilService
from agent_overlord.services.controller_runtime import ControllerContainerPool
from agent_overlord.services.council_scheduler import CouncilScheduler
from agent_overlord.services.inventory import InventoryService
from agent_overlord.services.prompts import PromptService
from agent_overlord.services.actions import PromptActionArbiter
from agent_overlord.storage.sqlite import SQLiteStore


class ControlPlane:
    """UI-independent owner of Agent Overlord's persistent runtime."""

    def __init__(
        self,
        config: AppConfig,
        store: SQLiteStore,
        *,
        enable_inventory: bool = True,
        stream_queue_size: int = 256,
    ) -> None:
        self.config = config
        self.store = store
        self.inventory = InventoryService(config, store)
        self.prompts = PromptService(store, self.inventory)
        self.actions = PromptActionArbiter(store, self.inventory)
        self.prompts.action_arbiter = self.actions
        self.council = CouncilService(store, self.inventory)
        self.broadcaster = EventBroadcaster(stream_queue_size)
        self.enable_inventory = enable_inventory
        self.controller_tokens = {
            item.controller_id: secrets.token_urlsafe(32)
            for item in config.controllers if item.enabled
        }
        self.controller_pool = (
            ControllerContainerPool(
                config, store, self.inventory, self.controller_tokens
            )
            if config.controller_runtime_enabled and self.controller_tokens
            else None
        )
        self.council_scheduler = (
            CouncilScheduler(
                config, store, self.inventory, self.controller_pool, self.publish_chat
            )
            if self.controller_pool
            else None
        )
        if self.council_scheduler:
            self.council_scheduler.on_complete = self.prompts.review_completed
            self.prompts.review_scheduler = self.council_scheduler
        self.started_at = datetime.now(UTC)
        self._inventory_task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        await self.store.initialize()
        await self.store.initialize_automation_settings(self.config.automation)
        await self.inventory.initialize()
        await self.prompts.initialize()
        self.inventory.on_event(self._wall_event)
        self.inventory.on_workers(self._worker_snapshot)
        self.inventory.on_workers(self.prompts.observe_workers)
        self._running = True
        if self.controller_pool:
            await self.controller_pool.start()
        if self.council_scheduler:
            await self.council_scheduler.start()
        if self.enable_inventory:
            self._inventory_task = asyncio.create_task(
                self.inventory.run(), name="agent-overlord-inventory"
            )

    async def stop(self) -> None:
        if not self._running:
            return
        if self.council_scheduler:
            await self.council_scheduler.stop()
        if self.controller_pool:
            await self.controller_pool.stop()
        self.inventory.stop()
        if self._inventory_task:
            try:
                await asyncio.wait_for(self._inventory_task, timeout=20)
            except TimeoutError:
                self._inventory_task.cancel()
                await asyncio.gather(self._inventory_task, return_exceptions=True)
        self._running = False

    async def refresh(self) -> None:
        await self.inventory.refresh()

    async def _wall_event(self, event: WallEvent) -> None:
        await self.broadcaster.publish(
            "wall_event", event.model_dump(mode="json"), event.event_id
        )
        if event.kind == "notification" and event.data.get("notification_id"):
            notification = await self.store.get_notification(
                str(event.data["notification_id"])
            )
            if notification:
                await self.broadcaster.publish(
                    "council_notification", notification.model_dump(mode="json")
                )
        elif event.kind == "controller_lifecycle":
            await self.broadcaster.publish("controller_state", event.data["controller"])
        elif event.kind == EventKind.CONTROLLER_LOG:
            await self.broadcaster.publish("controller_log", event.data)
        elif event.kind in {EventKind.PROMPT, EventKind.ACTION}:
            prompt_id = str(event.data.get("prompt_id", ""))
            prompt = await self.store.get_prompt(prompt_id) if prompt_id else None
            if prompt:
                await self.broadcaster.publish(
                    "prompt", prompt.model_dump(mode="json"), prompt.prompt_id
                )

    async def _worker_snapshot(self, workers: list[Worker]) -> None:
        await self.broadcaster.publish(
            "workers",
            {"workers": [worker.model_dump(mode="json") for worker in workers]},
        )
        await self.broadcaster.publish(
            "health", (await self.health()).model_dump(mode="json")
        )

    async def health(self) -> HealthResponse:
        workers = list(self.inventory.workers.values())
        hosts = []
        for host in self.config.hosts:
            error = self.inventory.host_errors.get(host.name)
            hosts.append(
                HostHealth(
                    name=host.name,
                    connected=error is None,
                    error=error,
                    worker_count=sum(
                        worker.observation.host == host.name for worker in workers
                    ),
                )
            )
        return HealthResponse(
            status="ok" if self._running else "stopped",
            inventory_running=self._inventory_task is not None
            and not self._inventory_task.done(),
            started_at=self.started_at,
            configured_hosts=len(self.config.hosts),
            workers=len(workers),
            stream_clients=self.broadcaster.client_count,
            hosts=hosts,
        )

    async def snapshot(self) -> Snapshot:
        events = await self.store.list_events(limit=500)
        memories = [
            memory for memory in await self.store.list_memories(include_inactive=True)
            if memory.status in {"active", "candidate"}
        ]
        chat = await self.store.list_chat_messages(limit=500)
        return Snapshot(
            workers=list(self.inventory.workers.values()),
            events=events,
            messages=[ChatMessage(role=role, message=message) for role, message in chat],
            memories=memories,
            health=await self.health(),
            controllers=await self.store.list_controller_states(),
            notifications=await self.store.list_notifications(limit=100),
            ignored_sessions=list(self.inventory.ignored_sessions.values()),
            prompts=await self.store.list_prompts(limit=200),
            policies=await self.store.list_policies(include_inactive=True),
            automation=await self.store.get_automation_settings(),
        )

    async def publish_chat(self, role: str, message: str) -> None:
        await self.broadcaster.publish(
            "chat_message", ChatMessage(role=role, message=message).model_dump()
        )

    async def publish_memory(self, action: str, memory: Memory) -> None:
        await self.broadcaster.publish(
            "memory",
            {"action": action, "memory": memory.model_dump(mode="json")},
        )
