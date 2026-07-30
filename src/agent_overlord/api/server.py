from __future__ import annotations

import asyncio
import json
import mimetypes
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from agent_overlord.api.models import (
    ChatHistoryResponse,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    EventsResponse,
    HealthResponse,
    IgnoredSessionsResponse,
    IgnoreSessionResponse,
    MemoriesResponse,
    MemoryCreateRequest,
    MemoryUpdateRequest,
    RefreshResponse,
    RestoreIgnoredSessionsResponse,
    Snapshot,
    ProposalDetail,
    WorkersResponse,
    ApprovalPolicyCreateRequest,
    AutomationSettingsUpdateRequest,
    PromptDecisionRequest,
    PromptReviewRequest,
)
from agent_overlord.domain.prompts import ApprovalPolicy, PolicyStatus
from agent_overlord.domain.memories import Memory, MemoryStatus
from agent_overlord.domain.events import EventKind, WallEvent
from agent_overlord.domain.workers import WorkerState
from agent_overlord.services.control_plane import ControlPlane
from agent_overlord.services.actions import ActionRejected
from agent_overlord.api.mcp import build_controller_mcp


class _EmbeddedUvicornServer(uvicorn.Server):
    """A second loopback listener without taking the parent server's signals."""

    @contextmanager
    def capture_signals(self):
        yield


def _sse(event: str, data: object, event_id: str | None = None) -> str:
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append("data: " + json.dumps(data, separators=(",", ":"), default=str))
    return "\n".join(lines) + "\n\n"


def create_app(
    control_plane: ControlPlane,
    *,
    static_dir: str | Path | None = None,
) -> FastAPI:
    controller_mcps = [
        build_controller_mcp(
            control_plane,
            controller,
            token=control_plane.controller_tokens[controller.controller_id],
        )
        for controller in control_plane.config.controllers
        if controller.enabled
    ]
    mcp_gateway = FastAPI(title="Agent Overlord controller gateway")
    for controller_mcp in controller_mcps:
        mcp_gateway.mount(
            f"/mcp/{controller_mcp.config.controller_id}",
            controller_mcp.app,
            name=f"mcp-{controller_mcp.config.controller_id}",
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            for controller_mcp in controller_mcps:
                await stack.enter_async_context(controller_mcp.server.session_manager.run())
            mcp_server = None
            mcp_task = None
            if control_plane.controller_pool:
                parsed = urlparse(control_plane.config.controller_mcp_url)
                mcp_server = _EmbeddedUvicornServer(
                    uvicorn.Config(
                        mcp_gateway,
                        host="127.0.0.1",
                        port=parsed.port or 8001,
                        log_level="warning",
                        access_log=False,
                    )
                )
                mcp_task = asyncio.create_task(
                    mcp_server.serve(), name="agent-overlord-mcp-gateway"
                )
                for _ in range(100):
                    if mcp_server.started:
                        break
                    if mcp_task.done():
                        await mcp_task
                    await asyncio.sleep(0.01)
                if not mcp_server.started:
                    mcp_server.should_exit = True
                    await asyncio.gather(mcp_task, return_exceptions=True)
                    raise RuntimeError("controller MCP gateway did not start")
            await control_plane.start()
            try:
                yield
            finally:
                await control_plane.stop()
                if mcp_server and mcp_task:
                    mcp_server.should_exit = True
                    await asyncio.gather(mcp_task, return_exceptions=True)

    app = FastAPI(
        title="Agent Overlord",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.control_plane = control_plane
    app.state.controller_mcps = controller_mcps

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return await control_plane.health()

    @app.get("/api/workers", response_model=WorkersResponse)
    async def workers() -> WorkersResponse:
        return WorkersResponse(workers=list(control_plane.inventory.workers.values()))

    @app.get("/api/workers/{worker_id}")
    async def worker_detail(worker_id: str):
        worker = control_plane.inventory.workers.get(worker_id)
        if worker is None:
            raise HTTPException(status_code=404, detail="worker not found")
        return worker

    @app.delete("/api/workers/{worker_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def forget_worker(worker_id: str) -> Response:
        worker = control_plane.inventory.workers.get(worker_id)
        if worker is None:
            raise HTTPException(status_code=404, detail="worker not found")
        if worker.state != WorkerState.DISCONNECTED:
            raise HTTPException(
                status_code=409,
                detail="only disconnected workers can be permanently forgotten",
            )
        await control_plane.inventory.forget_worker(worker_id)
        await control_plane.inventory.emit(
            WallEvent(
                actor="user",
                kind=EventKind.SYSTEM,
                host=worker.observation.host,
                message=f"Permanently forgot {worker.observation.display_name}",
            )
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/api/workers/{worker_id}/ignore-session",
        response_model=IgnoreSessionResponse,
    )
    async def ignore_worker_session(worker_id: str) -> IgnoreSessionResponse:
        result = await control_plane.inventory.ignore_worker_session(worker_id)
        if result is None:
            raise HTTPException(status_code=404, detail="worker not found")
        ignored, removed = result
        await control_plane.inventory.emit(
            WallEvent(
                actor="user",
                kind=EventKind.SYSTEM,
                host=ignored.host,
                message=f"Ignoring tmux session {ignored.session_name}",
                data={"ignore_id": ignored.ignore_id, "session_id": ignored.session_id},
            )
        )
        return IgnoreSessionResponse(
            ignored_session=ignored,
            removed_worker_ids=[worker.worker_id for worker in removed],
        )

    @app.get("/api/ignored-sessions", response_model=IgnoredSessionsResponse)
    async def ignored_sessions() -> IgnoredSessionsResponse:
        return IgnoredSessionsResponse(
            ignored_sessions=list(control_plane.inventory.ignored_sessions.values())
        )

    @app.post(
        "/api/ignored-sessions/restore-all",
        response_model=RestoreIgnoredSessionsResponse,
    )
    async def restore_all_ignored_sessions() -> RestoreIgnoredSessionsResponse:
        restored_ids, workers = (
            await control_plane.inventory.restore_all_ignored_sessions()
        )
        await control_plane.inventory.emit(
            WallEvent(
                actor="user",
                kind=EventKind.SYSTEM,
                message=(
                    f"Restored {len(restored_ids)} excluded tmux "
                    f"session{'s' if len(restored_ids) != 1 else ''} and reconciled inventory"
                ),
                data={"restored_ignore_ids": restored_ids},
            )
        )
        return RestoreIgnoredSessionsResponse(
            restored_ignore_ids=restored_ids,
            workers=workers,
        )

    @app.delete(
        "/api/ignored-sessions/{ignore_id}", status_code=status.HTTP_204_NO_CONTENT
    )
    async def unignore_session(ignore_id: str) -> Response:
        if not await control_plane.inventory.unignore_session(ignore_id):
            raise HTTPException(status_code=404, detail="ignored session not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/events", response_model=EventsResponse)
    async def events(
        limit: int = Query(default=500, ge=1, le=5000),
        worker_id: str | None = None,
    ) -> EventsResponse:
        return EventsResponse(
            events=await control_plane.store.list_events(limit, worker_id)
        )

    @app.get("/api/controllers")
    async def controllers():
        return {"controllers": await control_plane.store.list_controller_states()}

    @app.get("/api/controllers/{controller_id}/logs")
    async def controller_logs(
        controller_id: str,
        tail: int = Query(default=200, ge=1, le=5000),
    ):
        if control_plane.controller_pool is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "controllers not enabled")
        log_file = control_plane.controller_pool.log_dir / f"{controller_id}.log"
        if not log_file.exists():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no logs for this controller")
        text = log_file.read_text(encoding="utf-8", errors="replace")
        entries = [e for e in text.split("\n\n") if e.strip()]
        return {"entries": entries[-tail:]}

    @app.get("/api/prompts")
    async def prompts(
        prompt_status: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=200, ge=1, le=2000),
    ):
        return {"prompts": await control_plane.store.list_prompts(
            status=prompt_status, limit=limit
        )}

    @app.get("/api/prompts/{prompt_id}")
    async def prompt_detail(prompt_id: str):
        item = await control_plane.store.get_prompt(prompt_id)
        if item is None:
            raise HTTPException(status_code=404, detail="prompt not found")
        return item

    @app.post("/api/prompts/{prompt_id}/decision")
    async def decide_prompt(prompt_id: str, request: PromptDecisionRequest):
        try:
            item = await control_plane.actions.decide(
                prompt_id,
                request.decision,
                request.choice,
                source="human",
                rationale=request.rationale,
                expected_fingerprint=request.expected_fingerprint,
                expected_worker_id=request.expected_worker_id,
                expected_pane_id=request.expected_pane_id,
            )
            if request.execute and request.decision != "escalate":
                item = await control_plane.actions.execute(item.prompt_id)
            return item
        except ActionRejected as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/prompts/{prompt_id}/execute")
    async def execute_prompt(prompt_id: str):
        try:
            return await control_plane.actions.execute(prompt_id)
        except ActionRejected as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/prompts/{prompt_id}/review")
    async def review_prompt(prompt_id: str, request: PromptReviewRequest):
        if control_plane.council_scheduler is None:
            raise HTTPException(status_code=409, detail="semantic council is disabled")
        try:
            notification = await control_plane.prompts.request_review(
                prompt_id, request.tier, control_plane.council_scheduler
            )
            return notification
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/approval-policies")
    async def approval_policies(include_inactive: bool = False):
        return {"policies": await control_plane.store.list_policies(include_inactive)}

    @app.post("/api/approval-policies", status_code=status.HTTP_201_CREATED)
    async def create_approval_policy(request: ApprovalPolicyCreateRequest):
        policy = ApprovalPolicy(
            **request.model_dump(), created_by="user", confirmed_at=datetime.now(UTC)
        )
        await control_plane.store.save_policy(policy)
        await control_plane.inventory.emit(
            WallEvent(
                actor="user", kind=EventKind.POLICY,
                message=f"Created approval policy: {policy.name}",
                data={"policy_id": policy.policy_id},
            )
        )
        return policy

    @app.delete("/api/approval-policies/{policy_id}")
    async def revoke_approval_policy(policy_id: str):
        policy = await control_plane.store.get_policy(policy_id)
        if policy is None:
            raise HTTPException(status_code=404, detail="policy not found")
        policy.status = PolicyStatus.REVOKED
        policy.revoked_at = policy.updated_at = datetime.now(UTC)
        await control_plane.store.save_policy(policy)
        await control_plane.inventory.emit(
            WallEvent(
                actor="user", kind=EventKind.POLICY,
                message=f"Revoked approval policy: {policy.name}",
                data={"policy_id": policy.policy_id, "status": policy.status},
            )
        )
        return policy

    @app.post("/api/approval-policies/{policy_id}/activate")
    async def activate_approval_policy(policy_id: str):
        policy = await control_plane.store.get_policy(policy_id)
        if policy is None:
            raise HTTPException(status_code=404, detail="policy not found")
        if policy.status not in {PolicyStatus.CANDIDATE, PolicyStatus.SUSPENDED}:
            raise HTTPException(status_code=409, detail="policy is not activatable")
        policy.status = PolicyStatus.ACTIVE
        policy.confirmed_at = policy.updated_at = datetime.now(UTC)
        await control_plane.store.save_policy(policy)
        await control_plane.inventory.emit(
            WallEvent(
                actor="user", kind=EventKind.POLICY,
                message=f"Activated approval policy: {policy.name}",
                data={"policy_id": policy.policy_id, "status": policy.status},
            )
        )
        return policy

    @app.get("/api/automation-settings")
    async def automation_settings():
        return await control_plane.store.get_automation_settings()

    @app.patch("/api/automation-settings")
    async def update_automation_settings(request: AutomationSettingsUpdateRequest):
        settings = await control_plane.store.get_automation_settings()
        for key, value in request.model_dump(exclude_none=True).items():
            setattr(settings, key, value)
        settings.updated_at = datetime.now(UTC)
        await control_plane.store.save_automation_settings(settings)
        await control_plane.prompts.apply_auto_yes_settings(settings)
        await control_plane.inventory.emit(
            WallEvent(
                actor="user", kind=EventKind.ACTION,
                message="Updated prompt automation settings",
                data={
                    "automation_enabled": settings.automation_enabled,
                    "dry_run": settings.dry_run,
                    "paused": settings.paused,
                    "auto_yes_workers": settings.auto_yes_workers,
                },
            )
        )
        return settings

    @app.get("/api/council/notifications")
    async def notifications(
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        return {"notifications": await control_plane.store.list_notifications(limit=limit)}

    @app.get("/api/council/notifications/{notification_id}")
    async def notification_detail(notification_id: str):
        item = await control_plane.store.get_notification(notification_id)
        if item is None:
            raise HTTPException(status_code=404, detail="notification not found")
        return item

    @app.get("/api/workers/{worker_id}/interpretations")
    async def worker_interpretations(
        worker_id: str, limit: int = Query(default=20, ge=1, le=200)
    ):
        if worker_id not in control_plane.inventory.workers:
            raise HTTPException(status_code=404, detail="worker not found")
        return {
            "interpretations": await control_plane.store.list_interpretations(
                worker_id, limit
            )
        }

    @app.get("/api/council/proposals")
    async def proposals(limit: int = Query(default=100, ge=1, le=1000)):
        return {"proposals": await control_plane.store.list_proposals(limit)}

    @app.get("/api/council/proposals/{proposal_id}", response_model=ProposalDetail)
    async def proposal_detail(proposal_id: str) -> ProposalDetail:
        proposal = await control_plane.store.get_proposal(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="proposal not found")
        return ProposalDetail(
            proposal=proposal,
            critiques=await control_plane.store.list_critiques(proposal_id),
            votes=await control_plane.store.list_votes(proposal_id),
        )

    @app.get("/api/chat", response_model=ChatHistoryResponse)
    async def chat_history(
        limit: int = Query(default=500, ge=1, le=5000),
    ) -> ChatHistoryResponse:
        records = await control_plane.store.list_chat_messages(limit)
        return ChatHistoryResponse(
            messages=[ChatMessage(role=role, message=message) for role, message in records]
        )

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        await control_plane.publish_chat("user", request.message)
        if control_plane.council_scheduler:
            await control_plane.store.add_chat_message("user", request.message.strip())
            await control_plane.inventory.emit(
                WallEvent(
                    actor="user",
                    kind=EventKind.HUMAN_MESSAGE,
                    message=request.message.strip(),
                    severity="human",
                )
            )
            matches = control_plane.council._contextual_workers(request.message.lower())
            worker_id = matches[0].worker_id if len(matches) == 1 else None
            notification = await control_plane.council_scheduler.enqueue_human_question(
                request.message.strip(), worker_id
            )
            return ChatResponse(
                message="The semantic council is investigating.",
                worker_ids=[worker_id] if worker_id else [],
                status="pending",
                notification_id=notification.notification_id,
            )
        result = await control_plane.council.handle(request.message)
        await control_plane.publish_chat("council", result.message)
        return ChatResponse(message=result.message, worker_ids=result.worker_ids)

    @app.get("/api/memories", response_model=MemoriesResponse)
    async def memories(query: str | None = None) -> MemoriesResponse:
        items = await control_plane.store.list_memories(query, include_inactive=True)
        return MemoriesResponse(memories=[
            memory for memory in items
            if memory.status in {MemoryStatus.ACTIVE, MemoryStatus.CANDIDATE}
        ])

    @app.post(
        "/api/memories",
        response_model=Memory,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_memory(request: MemoryCreateRequest) -> Memory:
        memory = Memory(
            claim=request.claim,
            scope=request.scope,
            kind=request.kind,
            source="web user instruction",
            created_by="user",
        )
        await control_plane.store.add_memory(memory)
        await control_plane.publish_memory("created", memory)
        return memory

    @app.patch("/api/memories/{memory_id}", response_model=Memory)
    async def update_memory(memory_id: str, request: MemoryUpdateRequest) -> Memory:
        matches = [
            memory for memory in await control_plane.store.get_memory(
                memory_id, include_inactive=True
            )
            if memory.status in {MemoryStatus.ACTIVE, MemoryStatus.CANDIDATE}
        ]
        if len(matches) != 1:
            raise HTTPException(status_code=404, detail="memory not found")
        memory = matches[0]
        memory.claim = request.claim
        memory.source = "web user correction"
        memory.updated_at = datetime.now(UTC)
        await control_plane.store.add_memory(memory)
        await control_plane.publish_memory("updated", memory)
        return memory

    @app.post("/api/memories/{memory_id}/activate", response_model=Memory)
    async def activate_memory(memory_id: str) -> Memory:
        matches = await control_plane.store.get_memory(
            memory_id, include_inactive=True
        )
        if len(matches) != 1:
            raise HTTPException(status_code=404, detail="memory not found")
        memory = matches[0]
        if memory.status != MemoryStatus.CANDIDATE:
            raise HTTPException(status_code=409, detail="memory is not a candidate")
        memory.status = MemoryStatus.ACTIVE
        memory.source = f"{memory.source}; approved by web user"
        memory.updated_at = datetime.now(UTC)
        await control_plane.store.add_memory(memory)
        await control_plane.publish_memory("updated", memory)
        return memory

    @app.delete("/api/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_memory(memory_id: str) -> Response:
        matches = [
            memory for memory in await control_plane.store.get_memory(
                memory_id, include_inactive=True
            )
            if memory.status in {MemoryStatus.ACTIVE, MemoryStatus.CANDIDATE}
        ]
        if len(matches) != 1:
            raise HTTPException(status_code=404, detail="memory not found")
        memory = matches[0]
        await control_plane.store.forget_memory(memory.memory_id)
        await control_plane.publish_memory("deleted", memory)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/api/inventory/refresh",
        response_model=RefreshResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def refresh_inventory() -> RefreshResponse:
        asyncio.create_task(control_plane.refresh())
        return RefreshResponse()

    @app.get("/api/snapshot", response_model=Snapshot)
    async def snapshot() -> Snapshot:
        return await control_plane.snapshot()

    @app.get("/api/stream")
    async def stream(request: Request) -> StreamingResponse:
        queue = await control_plane.broadcaster.subscribe()

        async def generate() -> AsyncIterator[str]:
            try:
                snapshot_value = await control_plane.snapshot()
                yield _sse("snapshot", snapshot_value.model_dump(mode="json"))
                yield _sse("ready", {"connected": True})
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield _sse("heartbeat", {"at": datetime.now(UTC).isoformat()})
                        continue
                    yield _sse(item.event, item.data, item.event_id)
            finally:
                await control_plane.broadcaster.unsubscribe(queue)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    dist = Path(static_dir) if static_dir else None
    if dist and dist.is_dir() and (dist / "index.html").is_file():
        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> Response:
            candidate = (dist / path).resolve()
            if (
                path
                and candidate.is_relative_to(dist.resolve())
                and candidate.is_file()
            ):
                media_type = mimetypes.guess_type(candidate.name)[0]
                return Response(candidate.read_bytes(), media_type=media_type)
            return Response(
                (dist / "index.html").read_bytes(), media_type="text/html"
            )

    return app
