from pathlib import Path

import httpx
import pytest

from agent_overlord.api.mcp import build_controller_mcp
from agent_overlord.config import AppConfig, ControllerConfig, HostConfig
from agent_overlord.domain.council import (
    ActionProposal,
    ControllerRole,
    ControllerRuntimeState,
    CouncilNotification,
    ProposalCritique,
    ProposalVote,
    SemanticInterpretation,
    VoteValue,
    NotificationStatus,
)
from agent_overlord.domain.workers import PaneObservation, Worker
from agent_overlord.services.control_plane import ControlPlane
from agent_overlord.services.council_tools import CouncilToolService
from agent_overlord.storage.sqlite import SQLiteStore


def worker() -> Worker:
    observation = PaneObservation(
        host="local", session_id="$1", session_name="work", window_id="@1",
        window_name="agent", pane_id="%1", current_command="codex",
        content=["Implementing the semantic council", "Allow uv run pytest?"],
    )
    return Worker(worker_id=observation.worker_id, observation=observation)


@pytest.mark.asyncio
async def test_council_records_round_trip_and_tools_reject_stale_evidence(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "council.db")
    plane = ControlPlane(
        AppConfig(hosts=[HostConfig(name="local", local=True)]), store,
        enable_inventory=False,
    )
    await plane.start()
    try:
        observed = worker()
        plane.inventory.workers[observed.worker_id] = observed
        tools = CouncilToolService(plane, "operator", ControllerRole.OPERATOR)
        capture = tools.get_worker_capture(observed.worker_id)
        interpretation = await tools.record_interpretation(
            observed.worker_id,
            capture["observation_fingerprint"],
            goal="Build a persistent semantic council",
            current_activity="Running tests",
            confidence=0.9,
            evidence=[{"kind": "capture", "reference": capture["observation_fingerprint"]}],
        )
        assert interpretation["version"] == 1
        assert (await store.list_interpretations(observed.worker_id))[0].goal

        with pytest.raises(ValueError, match="stale"):
            await tools.record_interpretation(
                observed.worker_id, "old-fingerprint", goal="Wrong"
            )

        notification = CouncilNotification(
            reason="human_question", human_message="What is its goal?",
            status=NotificationStatus.RUNNING,
        )
        await store.save_notification(notification)
        with pytest.raises(ValueError, match="only the strategist"):
            await tools.answer_human_message(
                notification.notification_id, "premature operator answer"
            )
        strategist_tools = CouncilToolService(
            plane, "strategist", ControllerRole.STRATEGIST
        )
        recorded = await strategist_tools.answer_human_message(
            notification.notification_id, "Its goal is council work."
        )
        assert recorded == {"recorded": True, "published": False}
        assert await store.list_chat_messages() == []
        duplicate = await strategist_tools.answer_human_message(
            notification.notification_id, "duplicate"
        )
        assert duplicate["recorded"] is False
        await tools.signal_done(notification.notification_id, "answered")
        stored_notification = (await store.list_notifications())[0]
        assert stored_notification.summary == "answered"
        assert stored_notification.answer == "Its goal is council work."

        candidate = await tools.propose_memory(
            "This project uses uv", scope="project:agent-overlord",
            evidence=[capture["observation_fingerprint"]],
        )
        assert candidate["status"] == "candidate"

        proposal = await tools.submit_proposal(
            "recommend approval", "The command is an in-scope test", "low",
            observed.worker_id, capture["observation_fingerprint"],
        )
        await tools.critique_proposal(proposal["proposal_id"], "Evidence is current")
        await tools.vote_on_proposal(
            proposal["proposal_id"], VoteValue.APPROVE, "Read-only recommendation"
        )
        await strategist_tools.vote_on_proposal(
            proposal["proposal_id"], VoteValue.REJECT,
            "The same evidence supports a more cautious recommendation",
        )
        assert (await store.get_proposal(proposal["proposal_id"])) is not None
        assert len(await store.list_critiques(proposal["proposal_id"])) == 1
        votes = await store.list_votes(proposal["proposal_id"])
        assert {vote.vote for vote in votes} == {
            VoteValue.APPROVE, VoteValue.REJECT
        }
    finally:
        await plane.stop()


@pytest.mark.asyncio
async def test_controller_mcp_requires_its_bearer_token(tmp_path: Path) -> None:
    plane = ControlPlane(
        AppConfig(hosts=[HostConfig(name="local", local=True)]),
        SQLiteStore(tmp_path / "auth.db"), enable_inventory=False,
    )
    config = ControllerConfig(
        controller_id="operator", role="operator", harness="claude.vertex", model="sonnet"
    )
    controller = build_controller_mcp(plane, config, token="secret-token")
    transport = httpx.ASGITransport(app=controller.app)
    async with controller.server.session_manager.run():
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.post("/mcp", json={})
            assert denied.status_code == 401
            # The MCP protocol rejects an empty request, but authorization has passed.
            allowed = await client.post(
                "/mcp", json={}, headers={"Authorization": "Bearer secret-token"}
            )
            assert allowed.status_code != 401


@pytest.mark.asyncio
async def test_council_can_find_list_and_read_session_files_safely(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    plans = project / "plans"
    plans.mkdir(parents=True)
    (plans / "PLAN.md").write_text("# Plan\n\nShip the reader.\n", encoding="utf-8")
    (project / "TASKS.txt").write_text("- add tests\n", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("not session context", encoding="utf-8")
    (project / "outside-link").symlink_to(outside)

    plane = ControlPlane(
        AppConfig(hosts=[HostConfig(name="local", local=True)]),
        SQLiteStore(tmp_path / "files.db"), enable_inventory=False,
    )
    await plane.start()
    try:
        observed = worker()
        observed.observation.current_path = str(project)
        plane.inventory.workers[observed.worker_id] = observed
        tools = CouncilToolService(plane, "operator", ControllerRole.OPERATOR)

        listing = await tools.list_session_directory(observed.worker_id)
        assert {item["path"] for item in listing["entries"]} == {
            "plans", "TASKS.txt", "outside-link"
        }
        found = await tools.find_session_files(
            observed.worker_id, "*.md", max_depth=3
        )
        assert found["matches"] == ["plans/PLAN.md"]
        regex_found = await tools.find_session_files(
            observed.worker_id, r".*/(PLAN\.md|TASKS\.txt)", pattern_type="regex"
        )
        assert set(regex_found["matches"]) == {"plans/PLAN.md", "TASKS.txt"}
        read = await tools.read_session_file(
            observed.worker_id, "plans/PLAN.md", max_bytes=12
        )
        assert read["content"].startswith("# Plan")
        assert read["truncated"] is True
        assert len(read["sha256"]) == 64
        assert read["read_at"].endswith("Z")

        with pytest.raises(ValueError, match="stay within"):
            await tools.read_session_file(observed.worker_id, "../secret.txt")
        with pytest.raises(ValueError, match="escapes"):
            await tools.read_session_file(observed.worker_id, "outside-link")
    finally:
        await plane.stop()


@pytest.mark.asyncio
async def test_all_typed_council_records_persist(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "records.db")
    await store.initialize()
    interpretation = SemanticInterpretation(
        worker_id="worker", observation_fingerprint="fingerprint",
        goal="goal", controller_id="operator",
    )
    notification = CouncilNotification(reason="test")
    proposal = ActionProposal(
        controller_id="operator", operation="recommend", rationale="reason"
    )
    critique = ProposalCritique(
        proposal_id=proposal.proposal_id, controller_id="auditor", findings="fine"
    )
    vote = ProposalVote(
        proposal_id=proposal.proposal_id, controller_id="auditor",
        vote=VoteValue.APPROVE, rationale="safe",
    )
    state = ControllerRuntimeState(
        controller_id="operator", role="operator", harness="claude.vertex", model="sonnet"
    )
    await store.add_interpretation(interpretation)
    await store.save_notification(notification)
    await store.save_proposal(proposal)
    await store.add_critique(critique)
    await store.add_vote(vote)
    await store.save_controller_state(state)
    assert (await store.list_interpretations("worker"))[0] == interpretation
    assert (await store.list_notifications())[0] == notification
    assert (await store.list_proposals())[0] == proposal
    assert (await store.list_critiques(proposal.proposal_id))[0] == critique
    assert (await store.list_votes(proposal.proposal_id))[0] == vote
    assert (await store.list_controller_states())[0] == state
