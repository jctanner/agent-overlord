from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from agent_overlord.domain.events import WallEvent
from agent_overlord.domain.ignored import IgnoredSession
from agent_overlord.domain.council import (
    ActionProposal,
    ControllerRuntimeState,
    CouncilNotification,
    ProposalCritique,
    ProposalVote,
    SemanticInterpretation,
)
from agent_overlord.domain.memories import Memory, MemoryStatus
from agent_overlord.domain.prompts import (
    ApprovalPolicy,
    AutomationSettings,
    PolicyStatus,
    PromptRequest,
)
from agent_overlord.domain.workers import Worker

T = TypeVar("T")


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS workers (
    worker_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ignored_sessions (
    ignore_id TEXT PRIMARY KEY,
    host TEXT NOT NULL,
    tmux_socket TEXT NOT NULL,
    session_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE(host, tmux_socket, session_id)
);
CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor TEXT NOT NULL,
    worker_id TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_created_at ON events(created_at);
CREATE INDEX IF NOT EXISTS events_worker_id ON events(worker_id);
CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    claim TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memories_scope_status ON memories(scope, status);
CREATE TABLE IF NOT EXISTS chat_messages (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS wall_positions (
    reader_id TEXT PRIMARY KEY,
    sequence INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS semantic_interpretations (
    interpretation_id TEXT PRIMARY KEY,
    worker_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS interpretations_worker_created
    ON semantic_interpretations(worker_id, created_at);
CREATE TABLE IF NOT EXISTS council_notifications (
    notification_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notifications_status_priority
    ON council_notifications(status, priority, created_at);
CREATE TABLE IF NOT EXISTS action_proposals (
    proposal_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposal_critiques (
    critique_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposal_votes (
    vote_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    controller_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE(proposal_id, controller_id)
);
CREATE TABLE IF NOT EXISTS controller_runtime (
    controller_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prompt_requests (
    prompt_id TEXT PRIMARY KEY,
    worker_id TEXT NOT NULL,
    pane_id TEXT NOT NULL,
    observation_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    risk TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE(worker_id, observation_fingerprint)
);
CREATE INDEX IF NOT EXISTS prompts_status_updated
    ON prompt_requests(status, updated_at);
CREATE INDEX IF NOT EXISTS prompts_worker_created
    ON prompt_requests(worker_id, created_at);
CREATE TABLE IF NOT EXISTS approval_policies (
    policy_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS automation_settings (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    payload TEXT NOT NULL
);
"""


class SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        await self._run(self._initialize_sync)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    async def _run(self, function: Callable[[], T]) -> T:
        # These transactions are deliberately small and use a local SQLite file.
        # Keeping them on the application loop also avoids lifecycle problems in
        # constrained terminals where Python's default thread executor may not
        # shut down cleanly. If write volume grows, this boundary can move behind
        # a dedicated serialized database worker without changing callers.
        return function()

    async def upsert_worker(self, worker: Worker) -> None:
        await self.upsert_workers([worker])

    async def upsert_workers(self, workers: list[Worker]) -> None:
        if not workers:
            return

        def operation() -> None:
            with self._connect() as connection:
                connection.executemany(
                    """INSERT INTO workers(worker_id, payload, last_seen_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(worker_id) DO UPDATE SET
                         payload=excluded.payload, last_seen_at=excluded.last_seen_at""",
                    [
                        (
                            worker.worker_id,
                            worker.model_dump_json(),
                            worker.last_seen_at.isoformat(),
                        )
                        for worker in workers
                    ],
                )

        await self._run(operation)

    async def list_workers(self) -> list[Worker]:
        def operation() -> list[Worker]:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT payload FROM workers ORDER BY last_seen_at DESC"
                ).fetchall()
            return [Worker.model_validate_json(row["payload"]) for row in rows]

        return await self._run(operation)

    async def delete_workers(self, worker_ids: list[str]) -> None:
        if not worker_ids:
            return

        def operation() -> None:
            with self._connect() as connection:
                connection.executemany(
                    "DELETE FROM workers WHERE worker_id = ?",
                    [(worker_id,) for worker_id in worker_ids],
                )

        await self._run(operation)

    async def add_ignored_session(self, ignored: IgnoredSession) -> None:
        def operation() -> None:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO ignored_sessions
                       (ignore_id, host, tmux_socket, session_id, payload)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(host, tmux_socket, session_id) DO UPDATE SET
                         payload=excluded.payload""",
                    (
                        ignored.ignore_id,
                        ignored.host,
                        ignored.tmux_socket,
                        ignored.session_id,
                        ignored.model_dump_json(),
                    ),
                )

        await self._run(operation)

    async def list_ignored_sessions(self) -> list[IgnoredSession]:
        def operation() -> list[IgnoredSession]:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT payload FROM ignored_sessions ORDER BY host, session_id"
                ).fetchall()
            return [IgnoredSession.model_validate_json(row["payload"]) for row in rows]

        return await self._run(operation)

    async def delete_ignored_session(self, ignore_id: str) -> bool:
        def operation() -> bool:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM ignored_sessions WHERE ignore_id = ?", (ignore_id,)
                )
                return cursor.rowcount > 0

        return await self._run(operation)

    async def add_event(self, event: WallEvent) -> None:
        def operation() -> None:
            with self._connect() as connection:
                connection.execute(
                    """INSERT OR IGNORE INTO events
                       (event_id, created_at, kind, actor, worker_id, payload)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        event.event_id,
                        event.created_at.isoformat(),
                        event.kind,
                        event.actor,
                        event.worker_id,
                        event.model_dump_json(),
                    ),
                )

        await self._run(operation)

    async def list_events(
        self, limit: int = 500, worker_id: str | None = None
    ) -> list[WallEvent]:
        def operation() -> list[WallEvent]:
            sql = "SELECT payload FROM events"
            params: list[object] = []
            if worker_id:
                sql += " WHERE worker_id = ?"
                params.append(worker_id)
            sql += " ORDER BY sequence DESC LIMIT ?"
            params.append(limit)
            with self._connect() as connection:
                rows = connection.execute(sql, params).fetchall()
            return [WallEvent.model_validate_json(row["payload"]) for row in reversed(rows)]

        return await self._run(operation)

    async def read_wall(self, reader_id: str, limit: int = 500) -> list[WallEvent]:
        """Return unread wall events and advance this reader's durable cursor."""

        def operation() -> list[WallEvent]:
            with self._connect() as connection:
                position = connection.execute(
                    "SELECT sequence FROM wall_positions WHERE reader_id = ?",
                    (reader_id,),
                ).fetchone()
                sequence = int(position["sequence"]) if position else 0
                rows = connection.execute(
                    """SELECT sequence, payload FROM events
                       WHERE sequence > ? ORDER BY sequence LIMIT ?""",
                    (sequence, limit),
                ).fetchall()
                if rows:
                    connection.execute(
                        """INSERT INTO wall_positions(reader_id, sequence) VALUES (?, ?)
                           ON CONFLICT(reader_id) DO UPDATE SET sequence=excluded.sequence""",
                        (reader_id, rows[-1]["sequence"]),
                    )
            return [WallEvent.model_validate_json(row["payload"]) for row in rows]

        return await self._run(operation)

    async def reset_wall_reader(self, reader_id: str) -> None:
        def operation() -> None:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM wall_positions WHERE reader_id = ?", (reader_id,)
                )

        await self._run(operation)

    async def add_memory(self, memory: Memory) -> None:
        def operation() -> None:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO memories
                       (memory_id, scope, kind, status, claim, payload, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(memory_id) DO UPDATE SET
                         scope=excluded.scope, kind=excluded.kind,
                         status=excluded.status, claim=excluded.claim,
                         payload=excluded.payload, updated_at=excluded.updated_at""",
                    (
                        memory.memory_id,
                        memory.scope,
                        memory.kind,
                        memory.status,
                        memory.claim,
                        memory.model_dump_json(),
                        memory.updated_at.isoformat(),
                    ),
                )

        await self._run(operation)

    async def list_memories(
        self, query: str | None = None, include_inactive: bool = False
    ) -> list[Memory]:
        def operation() -> list[Memory]:
            clauses: list[str] = []
            params: list[object] = []
            if not include_inactive:
                clauses.append("status = ?")
                params.append(MemoryStatus.ACTIVE)
            if query:
                clauses.append("lower(claim) LIKE ?")
                params.append(f"%{query.lower()}%")
            sql = "SELECT payload FROM memories"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY updated_at DESC"
            with self._connect() as connection:
                rows = connection.execute(sql, params).fetchall()
            return [Memory.model_validate_json(row["payload"]) for row in rows]

        return await self._run(operation)

    async def forget_memory(self, memory_id: str) -> bool:
        def operation() -> bool:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload FROM memories WHERE memory_id = ?", (memory_id,)
                ).fetchone()
                if row is None:
                    return False
                memory = Memory.model_validate_json(row["payload"])
                memory.status = MemoryStatus.SUPERSEDED
                connection.execute(
                    "UPDATE memories SET status=?, payload=? WHERE memory_id=?",
                    (memory.status, memory.model_dump_json(), memory_id),
                )
                return True

        return await self._run(operation)

    async def get_memory(
        self, memory_id_prefix: str, include_inactive: bool = False
    ) -> list[Memory]:
        memories = await self.list_memories(include_inactive=include_inactive)
        return [item for item in memories if item.memory_id.startswith(memory_id_prefix)]

    async def add_chat_message(self, role: str, message: str) -> None:
        def operation() -> None:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO chat_messages(role, message) VALUES (?, ?)",
                    (role, message),
                )

        await self._run(operation)

    async def list_chat_messages(self, limit: int = 200) -> list[tuple[str, str]]:
        def operation() -> list[tuple[str, str]]:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT role, message FROM chat_messages ORDER BY sequence DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [(row["role"], row["message"]) for row in reversed(rows)]

        return await self._run(operation)

    async def add_interpretation(self, item: SemanticInterpretation) -> None:
        def operation() -> None:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO semantic_interpretations
                       (interpretation_id, worker_id, created_at, payload)
                       VALUES (?, ?, ?, ?)""",
                    (
                        item.interpretation_id,
                        item.worker_id,
                        item.created_at.isoformat(),
                        item.model_dump_json(),
                    ),
                )

        await self._run(operation)

    async def list_interpretations(
        self, worker_id: str, limit: int = 20
    ) -> list[SemanticInterpretation]:
        def operation() -> list[SemanticInterpretation]:
            with self._connect() as connection:
                rows = connection.execute(
                    """SELECT payload FROM semantic_interpretations
                       WHERE worker_id = ? ORDER BY created_at DESC LIMIT ?""",
                    (worker_id, limit),
                ).fetchall()
            return [SemanticInterpretation.model_validate_json(row["payload"]) for row in rows]

        return await self._run(operation)

    async def save_notification(self, item: CouncilNotification) -> None:
        def operation() -> None:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO council_notifications
                       (notification_id, status, priority, created_at, payload)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(notification_id) DO UPDATE SET
                         status=excluded.status, priority=excluded.priority,
                         payload=excluded.payload""",
                    (
                        item.notification_id,
                        item.status,
                        item.priority,
                        item.created_at.isoformat(),
                        item.model_dump_json(),
                    ),
                )

        await self._run(operation)

    async def list_notifications(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[CouncilNotification]:
        def operation() -> list[CouncilNotification]:
            sql = "SELECT payload FROM council_notifications"
            params: list[object] = []
            if status:
                sql += " WHERE status = ?"
                params.append(status)
            sql += " ORDER BY priority DESC, created_at LIMIT ?"
            params.append(limit)
            with self._connect() as connection:
                rows = connection.execute(sql, params).fetchall()
            return [CouncilNotification.model_validate_json(row["payload"]) for row in rows]

        return await self._run(operation)

    async def get_notification(
        self, notification_id: str
    ) -> CouncilNotification | None:
        def operation() -> CouncilNotification | None:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload FROM council_notifications WHERE notification_id = ?",
                    (notification_id,),
                ).fetchone()
            return CouncilNotification.model_validate_json(row["payload"]) if row else None

        return await self._run(operation)

    async def save_proposal(self, item: ActionProposal) -> None:
        def operation() -> None:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO action_proposals
                       (proposal_id, status, created_at, payload) VALUES (?, ?, ?, ?)
                       ON CONFLICT(proposal_id) DO UPDATE SET
                         status=excluded.status, payload=excluded.payload""",
                    (
                        item.proposal_id,
                        item.status,
                        item.created_at.isoformat(),
                        item.model_dump_json(),
                    ),
                )

        await self._run(operation)

    async def get_proposal(self, proposal_id: str) -> ActionProposal | None:
        def operation() -> ActionProposal | None:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload FROM action_proposals WHERE proposal_id = ?",
                    (proposal_id,),
                ).fetchone()
            return ActionProposal.model_validate_json(row["payload"]) if row else None

        return await self._run(operation)

    async def list_proposals(self, limit: int = 100) -> list[ActionProposal]:
        def operation() -> list[ActionProposal]:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT payload FROM action_proposals ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [ActionProposal.model_validate_json(row["payload"]) for row in rows]

        return await self._run(operation)

    async def add_critique(self, item: ProposalCritique) -> None:
        def operation() -> None:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO proposal_critiques
                       (critique_id, proposal_id, created_at, payload)
                       VALUES (?, ?, ?, ?)""",
                    (
                        item.critique_id,
                        item.proposal_id,
                        item.created_at.isoformat(),
                        item.model_dump_json(),
                    ),
                )

        await self._run(operation)

    async def list_critiques(
        self, proposal_id: str, limit: int = 100
    ) -> list[ProposalCritique]:
        def operation() -> list[ProposalCritique]:
            with self._connect() as connection:
                rows = connection.execute(
                    """SELECT payload FROM proposal_critiques
                       WHERE proposal_id = ? ORDER BY created_at LIMIT ?""",
                    (proposal_id, limit),
                ).fetchall()
            return [ProposalCritique.model_validate_json(row["payload"]) for row in rows]

        return await self._run(operation)

    async def add_vote(self, item: ProposalVote) -> None:
        def operation() -> None:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO proposal_votes
                       (vote_id, proposal_id, controller_id, created_at, payload)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(proposal_id, controller_id) DO UPDATE SET
                         vote_id=excluded.vote_id, created_at=excluded.created_at,
                         payload=excluded.payload""",
                    (
                        item.vote_id,
                        item.proposal_id,
                        item.controller_id,
                        item.created_at.isoformat(),
                        item.model_dump_json(),
                    ),
                )

        await self._run(operation)

    async def list_votes(
        self, proposal_id: str, limit: int = 100
    ) -> list[ProposalVote]:
        def operation() -> list[ProposalVote]:
            with self._connect() as connection:
                rows = connection.execute(
                    """SELECT payload FROM proposal_votes
                       WHERE proposal_id = ? ORDER BY created_at LIMIT ?""",
                    (proposal_id, limit),
                ).fetchall()
            return [ProposalVote.model_validate_json(row["payload"]) for row in rows]

        return await self._run(operation)

    async def save_controller_state(self, item: ControllerRuntimeState) -> None:
        def operation() -> None:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO controller_runtime(controller_id, status, payload)
                       VALUES (?, ?, ?) ON CONFLICT(controller_id) DO UPDATE SET
                         status=excluded.status, payload=excluded.payload""",
                    (item.controller_id, item.status, item.model_dump_json()),
                )

        await self._run(operation)

    async def list_controller_states(self) -> list[ControllerRuntimeState]:
        def operation() -> list[ControllerRuntimeState]:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT payload FROM controller_runtime ORDER BY controller_id"
                ).fetchall()
            return [ControllerRuntimeState.model_validate_json(row["payload"]) for row in rows]

        return await self._run(operation)

    async def save_prompt(self, item: PromptRequest) -> None:
        def operation() -> None:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO prompt_requests
                       (prompt_id, worker_id, pane_id, observation_fingerprint,
                        status, risk, created_at, updated_at, payload)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(prompt_id) DO UPDATE SET
                         status=excluded.status, risk=excluded.risk,
                         updated_at=excluded.updated_at, payload=excluded.payload""",
                    (
                        item.prompt_id,
                        item.worker_id,
                        item.pane_id,
                        item.observation_fingerprint,
                        item.status,
                        item.risk,
                        item.created_at.isoformat(),
                        item.updated_at.isoformat(),
                        item.model_dump_json(),
                    ),
                )

        await self._run(operation)

    async def get_prompt(self, prompt_id: str) -> PromptRequest | None:
        def operation() -> PromptRequest | None:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload FROM prompt_requests WHERE prompt_id = ?",
                    (prompt_id,),
                ).fetchone()
            return PromptRequest.model_validate_json(row["payload"]) if row else None

        return await self._run(operation)

    async def get_prompt_for_observation(
        self, worker_id: str, observation_fingerprint: str
    ) -> PromptRequest | None:
        def operation() -> PromptRequest | None:
            with self._connect() as connection:
                row = connection.execute(
                    """SELECT payload FROM prompt_requests
                       WHERE worker_id = ? AND observation_fingerprint = ?""",
                    (worker_id, observation_fingerprint),
                ).fetchone()
            return PromptRequest.model_validate_json(row["payload"]) if row else None

        return await self._run(operation)

    async def list_prompts(
        self, *, status: str | None = None, limit: int = 200
    ) -> list[PromptRequest]:
        def operation() -> list[PromptRequest]:
            sql = "SELECT payload FROM prompt_requests"
            params: list[object] = []
            if status:
                sql += " WHERE status = ?"
                params.append(status)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            with self._connect() as connection:
                rows = connection.execute(sql, params).fetchall()
            return [PromptRequest.model_validate_json(row["payload"]) for row in rows]

        return await self._run(operation)

    async def save_policy(self, item: ApprovalPolicy) -> None:
        def operation() -> None:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO approval_policies(policy_id, status, updated_at, payload)
                       VALUES (?, ?, ?, ?) ON CONFLICT(policy_id) DO UPDATE SET
                         status=excluded.status, updated_at=excluded.updated_at,
                         payload=excluded.payload""",
                    (
                        item.policy_id,
                        item.status,
                        item.updated_at.isoformat(),
                        item.model_dump_json(),
                    ),
                )

        await self._run(operation)

    async def get_policy(self, policy_id: str) -> ApprovalPolicy | None:
        def operation() -> ApprovalPolicy | None:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload FROM approval_policies WHERE policy_id = ?",
                    (policy_id,),
                ).fetchone()
            return ApprovalPolicy.model_validate_json(row["payload"]) if row else None

        return await self._run(operation)

    async def list_policies(self, include_inactive: bool = False) -> list[ApprovalPolicy]:
        def operation() -> list[ApprovalPolicy]:
            sql = "SELECT payload FROM approval_policies"
            params: list[object] = []
            if not include_inactive:
                sql += " WHERE status = ?"
                params.append(PolicyStatus.ACTIVE)
            sql += " ORDER BY updated_at DESC"
            with self._connect() as connection:
                rows = connection.execute(sql, params).fetchall()
            return [ApprovalPolicy.model_validate_json(row["payload"]) for row in rows]

        return await self._run(operation)

    async def get_automation_settings(self) -> AutomationSettings:
        def operation() -> AutomationSettings:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload FROM automation_settings WHERE singleton = 1"
                ).fetchone()
            return (
                AutomationSettings.model_validate_json(row["payload"])
                if row else AutomationSettings()
            )

        return await self._run(operation)

    async def save_automation_settings(self, item: AutomationSettings) -> None:
        def operation() -> None:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO automation_settings(singleton, payload) VALUES (1, ?)
                       ON CONFLICT(singleton) DO UPDATE SET payload=excluded.payload""",
                    (item.model_dump_json(),),
                )

        await self._run(operation)

    async def initialize_automation_settings(self, item: AutomationSettings) -> None:
        def operation() -> None:
            with self._connect() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO automation_settings(singleton, payload) VALUES (1, ?)",
                    (item.model_dump_json(),),
                )

        await self._run(operation)

    async def export_snapshot(self) -> dict[str, object]:
        workers = await self.list_workers()
        events = await self.list_events()
        memories = await self.list_memories()
        return {
            "workers": [json.loads(worker.model_dump_json()) for worker in workers],
            "events": [json.loads(event.model_dump_json()) for event in events],
            "memories": [json.loads(memory.model_dump_json()) for memory in memories],
        }
