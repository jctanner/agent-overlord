from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from agent_overlord.config import AppConfig
from agent_overlord.domain.events import EventKind, WallEvent
from agent_overlord.domain.ignored import IgnoredSession
from agent_overlord.domain.workers import Worker, WorkerState
from agent_overlord.services.classifier import WorkerClassifier
from agent_overlord.services.discovery import TmuxDiscovery
from agent_overlord.storage.sqlite import SQLiteStore


EventListener = Callable[[WallEvent], Awaitable[None]]
WorkerListener = Callable[[list[Worker]], Awaitable[None]]


class InventoryService:
    def __init__(self, config: AppConfig, store: SQLiteStore) -> None:
        self.config = config
        self.store = store
        self.discovery = TmuxDiscovery(config)
        self.classifier = WorkerClassifier(config)
        self.workers: dict[str, Worker] = {}
        self.host_errors: dict[str, str] = {}
        self.ignored_sessions: dict[str, IgnoredSession] = {}
        self._event_listeners: list[EventListener] = []
        self._worker_listeners: list[WorkerListener] = []
        self._stop = asyncio.Event()
        self._refresh_lock = asyncio.Lock()
        self._missing_counts: dict[str, int] = {}

    async def initialize(self) -> None:
        self.workers = {worker.worker_id: worker for worker in await self.store.list_workers()}
        ignored = await self.store.list_ignored_sessions()
        self.ignored_sessions = {item.ignore_id: item for item in ignored}
        excluded = [
            worker_id for worker_id, worker in self.workers.items()
            if self._is_ignored(worker.observation)
        ]
        for worker_id in excluded:
            self.workers.pop(worker_id, None)
        await self.store.delete_workers(excluded)

    def _is_ignored(self, observation) -> bool:
        return any(
            ignored.matches(
                observation.host, observation.tmux_socket, observation.session_id
            )
            for ignored in self.ignored_sessions.values()
        )

    async def forget_worker(self, worker_id: str) -> Worker | None:
        async with self._refresh_lock:
            worker = self.workers.pop(worker_id, None)
            if worker is None:
                return None
            await self.store.delete_workers([worker_id])
            await self._notify_workers()
            return worker

    async def ignore_worker_session(self, worker_id: str) -> tuple[IgnoredSession, list[Worker]] | None:
        async with self._refresh_lock:
            worker = self.workers.get(worker_id)
            if worker is None:
                return None
            observation = worker.observation
            ignored = IgnoredSession(
                host=observation.host,
                tmux_socket=observation.tmux_socket,
                session_id=observation.session_id,
                session_name=observation.session_name,
            )
            await self.store.add_ignored_session(ignored)
            self.ignored_sessions[ignored.ignore_id] = ignored
            removed = [
                item for item in self.workers.values()
                if ignored.matches(
                    item.observation.host,
                    item.observation.tmux_socket,
                    item.observation.session_id,
                )
            ]
            for item in removed:
                self.workers.pop(item.worker_id, None)
            await self.store.delete_workers([item.worker_id for item in removed])
            await self._notify_workers()
            return ignored, removed

    async def unignore_session(self, ignore_id: str) -> bool:
        async with self._refresh_lock:
            if not await self.store.delete_ignored_session(ignore_id):
                return False
            self.ignored_sessions.pop(ignore_id, None)
            return True

    async def restore_all_ignored_sessions(self) -> tuple[list[str], list[Worker]]:
        """Clear every persistent exclusion and immediately rebuild inventory."""
        async with self._refresh_lock:
            restored_ids = list(self.ignored_sessions)
            for ignore_id in restored_ids:
                await self.store.delete_ignored_session(ignore_id)
            self.ignored_sessions.clear()
            workers = await self._refresh_unlocked()
            return restored_ids, workers

    def on_event(self, listener: EventListener) -> None:
        self._event_listeners.append(listener)

    def on_workers(self, listener: WorkerListener) -> None:
        self._worker_listeners.append(listener)

    async def emit(self, event: WallEvent) -> None:
        await self.store.add_event(event)
        await asyncio.gather(*(listener(event) for listener in self._event_listeners))

    async def refresh(self) -> list[Worker]:
        async with self._refresh_lock:
            return await self._refresh_unlocked()

    async def _refresh_unlocked(self) -> list[Worker]:
        observations_by_host, errors = await self.discovery.discover_all()
        seen: set[str] = set()
        observed_workers: list[Worker] = []
        for host, observations in observations_by_host.items():
            if host in self.host_errors:
                await self.emit(
                    WallEvent(
                        actor="observer",
                        kind=EventKind.RECOVERED,
                        host=host,
                        message=f"Connection to {host} recovered",
                    )
                )
            self.host_errors.pop(host, None)
            for observation in observations:
                if self._is_ignored(observation):
                    continue
                previous = self.workers.get(observation.worker_id)
                # Once a pane is known to host an agent, keep observing it even
                # when the latest capture no longer contains a harness signature.
                # Otherwise quiet shells flap between disconnected and idle as
                # identifying scrollback moves beyond the capture window.
                if previous is None and not self.classifier.is_agent(observation):
                    continue
                worker = self.classifier.classify(observation, previous)
                seen.add(worker.worker_id)
                self._missing_counts.pop(worker.worker_id, None)
                self.workers[worker.worker_id] = worker
                observed_workers.append(worker)
                await self._emit_changes(previous, worker)
                # Give Textual an opportunity to drain terminal input between
                # classification units even when a fleet observation is large.
                await asyncio.sleep(0)

        await self.store.upsert_workers(observed_workers)

        for host, error in errors.items():
            if self.host_errors.get(host) != error:
                await self.emit(
                    WallEvent(
                        actor="observer",
                        kind=EventKind.DISCONNECTED,
                        host=host,
                        severity="error",
                        message=f"Cannot inventory {host}: {error}",
                    )
                )
            self.host_errors[host] = error
            await self._mark_host_disconnected(host)

        successful_hosts = set(observations_by_host)
        for worker in self.workers.values():
            if (
                worker.observation.host in successful_hosts
                and worker.worker_id not in seen
                and worker.state != WorkerState.DISCONNECTED
            ):
                misses = self._missing_counts.get(worker.worker_id, 0) + 1
                self._missing_counts[worker.worker_id] = misses
                if misses < self.config.disappearance_confirmations:
                    continue
                worker.state = WorkerState.DISCONNECTED
                worker.evidence = [
                    "Pane was absent from "
                    f"{misses} consecutive successful inventory snapshots"
                ]
                await self.store.upsert_worker(worker)
                await self.emit(
                    WallEvent(
                        actor="observer",
                        kind=EventKind.DISCONNECTED,
                        worker_id=worker.worker_id,
                        host=worker.observation.host,
                        message=f"Pane disappeared: {worker.observation.display_name}",
                    )
                )

        ordered = self._ordered_workers()
        await asyncio.gather(*(listener(ordered) for listener in self._worker_listeners))
        return ordered

    def _ordered_workers(self) -> list[Worker]:
        return sorted(
            self.workers.values(),
            key=lambda item: (
                self._state_order(item.state),
                item.observation.host,
                item.observation.display_name,
            ),
        )
    async def _notify_workers(self) -> None:
        ordered = self._ordered_workers()
        await asyncio.gather(*(listener(ordered) for listener in self._worker_listeners))

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.refresh()
            try:
                await asyncio.wait_for(self._stop.wait(), self.config.poll_interval_secs)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()

    async def _emit_changes(self, previous: Worker | None, worker: Worker) -> None:
        if previous is None:
            await self.emit(
                WallEvent(
                    actor="observer",
                    kind=EventKind.DISCOVERED,
                    worker_id=worker.worker_id,
                    host=worker.observation.host,
                    message=f"Discovered {worker.harness} at {worker.observation.display_name}",
                )
            )
            return
        if previous.state != worker.state:
            await self.emit(
                WallEvent(
                    actor="observer",
                    kind=(
                        EventKind.INPUT_REQUESTED
                        if worker.state == WorkerState.AWAITING_INPUT
                        else EventKind.STATE_CHANGED
                    ),
                    worker_id=worker.worker_id,
                    host=worker.observation.host,
                    message=(
                        f"{worker.observation.display_name}: "
                        f"{previous.state.value} → {worker.state.value}"
                    ),
                    data={"old_state": previous.state, "new_state": worker.state},
                )
            )
        if previous.purpose != worker.purpose:
            await self.emit(
                WallEvent(
                    actor="observer",
                    kind=EventKind.PURPOSE_CHANGED,
                    worker_id=worker.worker_id,
                    host=worker.observation.host,
                    message=f"Purpose changed: {worker.purpose}",
                )
            )

    async def _mark_host_disconnected(self, host: str) -> None:
        now = datetime.now(UTC)
        for worker in self.workers.values():
            if worker.observation.host != host or worker.state == WorkerState.DISCONNECTED:
                continue
            worker.state = WorkerState.DISCONNECTED
            worker.last_seen_at = now
            worker.evidence = [f"Host inventory failed: {self.host_errors[host]}"]
            await self.store.upsert_worker(worker)

    @staticmethod
    def _state_order(state: WorkerState) -> int:
        order = {
            WorkerState.AWAITING_INPUT: 0,
            WorkerState.FAILED: 1,
            WorkerState.STALLED: 2,
            WorkerState.DISCONNECTED: 3,
            WorkerState.ACTIVE: 4,
            WorkerState.IDLE: 5,
            WorkerState.COMPLETE: 6,
            WorkerState.UNKNOWN: 7,
        }
        return order[state]
