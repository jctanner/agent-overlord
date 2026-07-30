from __future__ import annotations

import secrets
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from agent_overlord.config import ControllerConfig
from agent_overlord.services.control_plane import ControlPlane
from agent_overlord.services.council_tools import CouncilToolService


@dataclass(slots=True)
class ControllerMCP:
    config: ControllerConfig
    token: str
    server: FastMCP
    app: ASGIApp


class BearerTokenMiddleware:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            supplied = headers.get(b"authorization", b"").decode(errors="replace")
            if not secrets.compare_digest(supplied, f"Bearer {self.token}"):
                await JSONResponse({"detail": "invalid controller token"}, status_code=401)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


def build_controller_mcp(
    control_plane: ControlPlane, config: ControllerConfig, token: str | None = None
) -> ControllerMCP:
    token = token or secrets.token_urlsafe(32)
    tools = CouncilToolService(control_plane, config.controller_id, config.role)
    server = FastMCP(
        name=f"Agent Overlord {config.role}",
        instructions=(
            "Use these tools to observe and deliberate. File tools provide bounded read-only "
            "access scoped to worker session directories. No tool can type into tmux or run "
            "arbitrary host commands. Cite observation fingerprints in consequential claims."
        ),
        stateless_http=False,
        json_response=True,
        streamable_http_path="/mcp",
    )

    server.tool()(tools.list_workers)
    server.tool()(tools.get_worker)
    server.tool()(tools.get_worker_capture)
    server.tool()(tools.get_worker_history)
    server.tool()(tools.get_host_health)
    server.tool()(tools.get_prompt)
    server.tool()(tools.review_prompt)
    server.tool()(tools.get_chat_context)
    server.tool()(tools.get_project_context)
    server.tool()(tools.list_session_directory)
    server.tool()(tools.find_session_files)
    server.tool()(tools.read_session_file)
    server.tool()(tools.search_wall)
    server.tool()(tools.search_memories)
    server.tool()(tools.propose_memory)
    server.tool()(tools.get_agentic_ledger)
    server.tool()(tools.get_interpretations)
    server.tool()(tools.record_interpretation)
    server.tool()(tools.post_wall_message)
    server.tool()(tools.submit_proposal)
    server.tool()(tools.critique_proposal)
    server.tool()(tools.vote_on_proposal)
    server.tool()(tools.answer_human_message)
    server.tool()(tools.signal_done)

    app = BearerTokenMiddleware(server.streamable_http_app(), token)
    return ControllerMCP(config=config, token=token, server=server, app=app)
