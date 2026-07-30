from pathlib import Path

from agent_overlord.domain.events import EventKind, WallEvent
from agent_overlord.domain.memories import Memory
from agent_overlord.services.classifier import WorkerClassifier
from agent_overlord.storage.sqlite import SQLiteStore


async def test_persists_workers_events_chat_and_memory(
    tmp_path: Path, config, codex_observation
) -> None:
    path = tmp_path / "state.db"
    store = SQLiteStore(path)
    await store.initialize()
    worker = WorkerClassifier(config).classify(codex_observation)
    event = WallEvent(
        actor="observer", kind=EventKind.DISCOVERED, message="worker found",
        worker_id=worker.worker_id,
    )
    memory = Memory(claim="Prefer Codex for personal projects")
    await store.upsert_worker(worker)
    await store.add_event(event)
    await store.add_memory(memory)
    await store.add_chat_message("user", "status")

    reopened = SQLiteStore(path)
    await reopened.initialize()
    assert (await reopened.list_workers())[0].worker_id == worker.worker_id
    assert (await reopened.list_events())[0].event_id == event.event_id
    assert (await reopened.list_memories())[0].claim == memory.claim
    assert await reopened.list_chat_messages() == [("user", "status")]

    assert [item.event_id for item in await reopened.read_wall("operator")] == [
        event.event_id
    ]
    assert await reopened.read_wall("operator") == []
    assert [item.event_id for item in await reopened.read_wall("auditor")] == [
        event.event_id
    ]

    assert await reopened.forget_memory(memory.memory_id)
    assert await reopened.list_memories() == []
    assert len(await reopened.list_memories(include_inactive=True)) == 1
