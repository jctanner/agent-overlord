from pathlib import Path

import httpx
import pytest

from agent_overlord.api.server import _sse, create_app
from agent_overlord.config import AppConfig, HostConfig
from agent_overlord.domain.workers import PaneObservation, Worker, WorkerState
from agent_overlord.domain.prompts import PromptStatus
from agent_overlord.domain.memories import Memory, MemoryStatus
from agent_overlord.services.control_plane import ControlPlane
from agent_overlord.storage.sqlite import SQLiteStore


def make_plane(tmp_path: Path) -> ControlPlane:
    config = AppConfig(hosts=[HostConfig(name="local", local=True)])
    return ControlPlane(config, SQLiteStore(tmp_path / "overlord.db"), enable_inventory=False)


def make_worker() -> Worker:
    observation = PaneObservation(
        host="local",
        session_id="$1",
        session_name="project",
        window_id="@1",
        window_name="agent",
        pane_id="%1",
        pane_title="codex",
        current_command="codex",
        content=["Working on the API", "`uv run pytest -q`", "Allow command? (y/n)"],
    )
    return Worker(
        worker_id=observation.worker_id,
        observation=observation,
        harness="codex",
        model="gpt-5",
        purpose="Build the local control plane",
        state=WorkerState.AWAITING_INPUT,
        awaiting_input=True,
    )


@pytest.mark.asyncio
async def test_typed_api_snapshot_chat_memory_and_lifecycle(tmp_path: Path) -> None:
    plane = make_plane(tmp_path)
    app = create_app(plane)
    await plane.start()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert plane.running
            worker = make_worker()
            plane.inventory.workers[worker.worker_id] = worker

            health = await client.get("/api/health")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"
            workers = (await client.get("/api/workers")).json()["workers"]
            assert workers[0]["harness"] == "codex"
            detail = await client.get(f"/api/workers/{worker.worker_id}")
            assert detail.status_code == 200
            assert (await client.get("/api/workers/missing")).status_code == 404

            response = await client.post("/api/chat", json={"message": "status"})
            assert response.status_code == 200
            assert "agent workers" in response.json()["message"]
            assert len((await client.get("/api/chat")).json()["messages"]) == 2

            created = await client.post(
                "/api/memories",
                json={"claim": "Use Codex for personal work", "scope": "routing"},
            )
            assert created.status_code == 201
            memory_id = created.json()["memory_id"]
            updated = await client.patch(
                f"/api/memories/{memory_id}",
                json={"claim": "Use Codex for personal projects"},
            )
            assert updated.json()["claim"].endswith("projects")
            deleted = await client.delete(f"/api/memories/{memory_id}")
            assert deleted.status_code == 204
            assert (await client.get("/api/memories")).json() == {"memories": []}

            candidate = Memory(
                claim="Never switch models on an agent request",
                status=MemoryStatus.CANDIDATE,
                created_by="strategist",
            )
            await plane.store.add_memory(candidate)
            listed = (await client.get("/api/memories")).json()["memories"]
            assert listed[0]["status"] == "candidate"
            activated = await client.post(
                f"/api/memories/{candidate.memory_id}/activate"
            )
            assert activated.status_code == 200
            assert activated.json()["status"] == "active"
            assert (await plane.store.get_memory(candidate.memory_id))[0].status == "active"
            repeated = await client.post(
                f"/api/memories/{candidate.memory_id}/activate"
            )
            assert repeated.status_code == 409

            snapshot = await client.get("/api/snapshot")
            assert snapshot.status_code == 200
            assert snapshot.json()["workers"][0]["worker_id"] == worker.worker_id

            assert (await client.delete(f"/api/workers/{worker.worker_id}")).status_code == 409
            worker.state = WorkerState.DISCONNECTED
            forgotten = await client.delete(f"/api/workers/{worker.worker_id}")
            assert forgotten.status_code == 204
            assert (await client.get("/api/workers")).json() == {"workers": []}
    finally:
        await plane.stop()
    assert not plane.running


@pytest.mark.asyncio
async def test_built_frontend_is_served_without_hiding_api(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<main>Agent Overlord</main>", encoding="utf-8")
    (assets / "app.js").write_text("console.log('ok')", encoding="utf-8")

    plane = make_plane(tmp_path)
    await plane.start()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(plane, static_dir=dist)),
            base_url="http://test",
        ) as client:
            assert "Agent Overlord" in (await client.get("/")).text
            assert "Agent Overlord" in (await client.get("/workers/one")).text
            assert "console.log" in (await client.get("/assets/app.js")).text
            assert (await client.get("/api/health")).json()["status"] == "ok"
    finally:
        await plane.stop()


def test_sse_serialization_includes_event_type_and_id() -> None:
    value = _sse("wall_event", {"message": "hello"}, "event-1")
    assert value == 'id: event-1\nevent: wall_event\ndata: {"message":"hello"}\n\n'


@pytest.mark.asyncio
async def test_prompt_policy_settings_and_dry_run_decision_api(tmp_path: Path) -> None:
    plane = make_plane(tmp_path)
    await plane.start()
    try:
        worker = make_worker()
        plane.inventory.workers[worker.worker_id] = worker
        await plane.prompts.observe_workers([worker])
        app = create_app(plane)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            prompts = (await client.get("/api/prompts")).json()["prompts"]
            assert len(prompts) == 1
            prompt = prompts[0]
            assert prompt["risk"] == "routine"

            policy_response = await client.post(
                "/api/approval-policies",
                json={
                    "name": "API tests", "decision": "allow", "match_kind": "exact",
                    "command_argv": prompt["normalized_argv"],
                    "allowed_choices": ["allow"], "project": "",
                    "risk_ceiling": "routine",
                },
            )
            assert policy_response.status_code == 201
            policy_id = policy_response.json()["policy_id"]
            assert len((await client.get("/api/approval-policies")).json()["policies"]) == 1

            settings = await client.patch(
                "/api/automation-settings", json={"paused": True}
            )
            assert settings.json()["paused"] is True
            await client.patch("/api/automation-settings", json={"paused": False})

            decided = await client.post(
                f"/api/prompts/{prompt['prompt_id']}/decision",
                json={
                    "decision": "allow", "choice": "allow",
                    "expected_fingerprint": prompt["observation_fingerprint"],
                    "expected_worker_id": prompt["worker_id"],
                    "expected_pane_id": prompt["pane_id"],
                    "execute": False,
                },
            )
            assert decided.status_code == 200
            assert decided.json()["status"] == PromptStatus.DECIDED
            assert (await client.post(
                f"/api/prompts/{prompt['prompt_id']}/execute"
            )).status_code == 409

            revoked = await client.delete(f"/api/approval-policies/{policy_id}")
            assert revoked.json()["status"] == "revoked"
    finally:
        await plane.stop()
