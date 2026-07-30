from pathlib import Path

from agent_overlord.domain.events import EventKind
from agent_overlord.services.inventory import InventoryService
from agent_overlord.storage.sqlite import SQLiteStore


async def test_inventory_discovers_worker_and_emits_changes(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "inventory.db")
    await store.initialize()
    inventory = InventoryService(config, store)
    await inventory.initialize()

    async def discovery_success():
        return {"local": [codex_observation]}, {}

    inventory.discovery.discover_all = discovery_success
    workers = await inventory.refresh()
    assert len(workers) == 1
    assert workers[0].awaiting_input
    kinds = [event.kind for event in await store.list_events()]
    assert EventKind.DISCOVERED in kinds

    codex_observation.content = ["Working now"]
    await inventory.refresh()
    kinds = [event.kind for event in await store.list_events()]
    assert EventKind.STATE_CHANGED in kinds


async def test_inventory_retains_workers_when_host_disconnects(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "inventory.db")
    await store.initialize()
    inventory = InventoryService(config, store)

    async def discovery_success():
        return {"local": [codex_observation]}, {}

    inventory.discovery.discover_all = discovery_success
    await inventory.refresh()

    async def discovery_failure():
        return {}, {"local": "connection failed"}

    inventory.discovery.discover_all = discovery_failure
    workers = await inventory.refresh()
    assert workers[0].state.value == "disconnected"
    assert "connection failed" in workers[0].evidence[0]


async def test_known_agent_does_not_flap_when_marker_scrolls_away(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "sticky.db")
    await store.initialize()
    inventory = InventoryService(config, store)

    async def first_discovery():
        return {"local": [codex_observation]}, {}

    inventory.discovery.discover_all = first_discovery
    worker = (await inventory.refresh())[0]

    quiet = codex_observation.model_copy(deep=True)
    quiet.current_command = "bash"
    quiet.start_command = ""
    quiet.pane_title = ""
    quiet.content = ["jtanner@example:~/work/example$"]

    async def quiet_discovery():
        return {"local": [quiet]}, {}

    inventory.discovery.discover_all = quiet_discovery
    refreshed = await inventory.refresh()
    assert refreshed[0].worker_id == worker.worker_id
    assert refreshed[0].state.value == "idle"
    assert refreshed[0].harness == "codex"
    assert all(event.kind != EventKind.DISCONNECTED for event in await store.list_events())


async def test_ignored_tmux_session_is_durable_and_suppresses_every_pane(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "ignored.db")
    await store.initialize()
    inventory = InventoryService(config, store)
    await inventory.initialize()
    second = codex_observation.model_copy(
        update={"pane_id": "%4", "pane_index": 1}, deep=True
    )

    async def discovery():
        return {"local": [codex_observation, second]}, {}

    inventory.discovery.discover_all = discovery
    workers = await inventory.refresh()
    assert len(workers) == 2

    result = await inventory.ignore_worker_session(workers[0].worker_id)
    assert result is not None
    ignored, removed = result
    assert len(removed) == 2
    assert await inventory.refresh() == []

    restarted = InventoryService(config, store)
    restarted.discovery.discover_all = discovery
    await restarted.initialize()
    assert await restarted.refresh() == []
    assert await restarted.unignore_session(ignored.ignore_id)
    assert len(await restarted.refresh()) == 2


async def test_restore_all_ignored_sessions_reconciles_inventory_immediately(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "restore-all.db")
    await store.initialize()
    inventory = InventoryService(config, store)
    await inventory.initialize()

    async def discovery():
        return {"local": [codex_observation]}, {}

    inventory.discovery.discover_all = discovery
    worker = (await inventory.refresh())[0]
    ignored, _ = (await inventory.ignore_worker_session(worker.worker_id))  # type: ignore[misc]

    restored_ids, workers = await inventory.restore_all_ignored_sessions()

    assert restored_ids == [ignored.ignore_id]
    assert [item.worker_id for item in workers] == [worker.worker_id]
    assert inventory.ignored_sessions == {}
    assert await store.list_ignored_sessions() == []


async def test_confirmed_missing_worker_is_pruned_from_inventory_and_storage(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "partial.db")
    await store.initialize()
    inventory = InventoryService(config, store)

    async def present():
        return {"local": [codex_observation]}, {}

    async def missing():
        return {"local": []}, {}

    inventory.discovery.discover_all = present
    worker = (await inventory.refresh())[0]
    inventory.discovery.discover_all = missing
    assert (await inventory.refresh())[0].state != "disconnected"
    assert (await inventory.refresh())[0].state != "disconnected"
    assert await inventory.refresh() == []
    assert await store.list_workers() == []
    events = await store.list_events()
    assert sum(event.kind == EventKind.DISCONNECTED for event in events) == 1


async def test_persisted_disconnected_worker_is_pruned_when_host_is_reachable(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "persisted-stale.db")
    await store.initialize()
    inventory = InventoryService(config, store)

    async def present():
        return {"local": [codex_observation]}, {}

    async def host_failure():
        return {}, {"local": "connection failed"}

    inventory.discovery.discover_all = present
    await inventory.refresh()
    inventory.discovery.discover_all = host_failure
    assert (await inventory.refresh())[0].state == "disconnected"

    restarted = InventoryService(config, store)
    await restarted.initialize()

    async def missing():
        return {"local": []}, {}

    restarted.discovery.discover_all = missing
    assert len(await restarted.refresh()) == 1
    assert len(await restarted.refresh()) == 1
    assert await restarted.refresh() == []
    assert await store.list_workers() == []
