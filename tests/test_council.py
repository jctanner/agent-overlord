from pathlib import Path

from agent_overlord.services.classifier import WorkerClassifier
from agent_overlord.services.council import CouncilService
from agent_overlord.services.inventory import InventoryService
from agent_overlord.storage.sqlite import SQLiteStore


async def test_council_queries_and_manages_memory(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "council.db")
    await store.initialize()
    inventory = InventoryService(config, store)
    worker = WorkerClassifier(config).classify(codex_observation)
    inventory.workers[worker.worker_id] = worker
    second_observation = codex_observation.model_copy(deep=True)
    second_observation.window_id = "@9"
    second_observation.pane_id = "%10"
    second_observation.window_name = "observatory"
    second = WorkerClassifier(config).classify(second_observation)
    inventory.workers[second.worker_id] = second
    council = CouncilService(store, inventory)

    status = await council.handle("what is blocked right now?")
    assert worker.worker_id in status.message
    remembered = await council.handle("remember [project:example] use uv run pytest")
    assert "Remembered" in remembered.message
    recalled = await council.handle("what do you remember about pytest?")
    assert "uv run pytest" in recalled.message
    memory = (await store.list_memories())[0]
    corrected = await council.handle(
        f"correct {memory.memory_id[:8]} to use uv run pytest -q"
    )
    assert "pytest -q" in corrected.message
    forgotten = await council.handle(f"forget {memory.memory_id[:8]}")
    assert "Forgot" in forgotten.message
    assert await store.list_memories() == []

    contextual = await council.handle("what is going on in the observatory session?")
    assert "1 matching worker" in contextual.message
    assert "observatory" in contextual.message
    assert "Host connection errors" not in contextual.message

    host_query = await council.handle("do we see a window on local?")
    assert "2 matching worker" in host_query.message
