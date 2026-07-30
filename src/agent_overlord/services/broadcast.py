from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class StreamEvent:
    event: str
    data: Any
    event_id: str


class EventBroadcaster:
    """Non-blocking bounded fan-out for SSE clients."""

    def __init__(self, queue_size: int = 256) -> None:
        self.queue_size = queue_size
        self._subscribers: set[asyncio.Queue[StreamEvent]] = set()
        self._lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        return len(self._subscribers)

    async def subscribe(self) -> asyncio.Queue[StreamEvent]:
        queue: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=self.queue_size)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[StreamEvent]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish(
        self, event: str, data: Any, event_id: str | None = None
    ) -> None:
        item = StreamEvent(event=event, data=data, event_id=event_id or uuid4().hex)
        # Never await a subscriber queue. A slow browser receives one resync
        # signal and must request a fresh authoritative snapshot.
        async with self._lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(item)
            except asyncio.QueueFull:
                while not subscriber.empty():
                    try:
                        subscriber.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                subscriber.put_nowait(
                    StreamEvent(
                        event="resync",
                        data={"reason": "client_buffer_overflow"},
                        event_id=uuid4().hex,
                    )
                )

