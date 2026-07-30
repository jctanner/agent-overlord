from __future__ import annotations

import re
from dataclasses import dataclass

from agent_overlord.domain.events import EventKind, WallEvent
from agent_overlord.domain.memories import Memory, MemoryKind
from agent_overlord.domain.workers import Worker, WorkerState
from agent_overlord.services.inventory import InventoryService
from agent_overlord.storage.sqlite import SQLiteStore


@dataclass(slots=True)
class CouncilResponse:
    message: str
    worker_ids: list[str]


class CouncilService:
    """Observation-first council facade.

    The MVP keeps this deterministic and evidence-backed. The interface is the
    stable seam where persistent Claude/Codex controllers can later contribute
    responses through the shared wall without gaining direct pane authority.
    """

    def __init__(self, store: SQLiteStore, inventory: InventoryService) -> None:
        self.store = store
        self.inventory = inventory

    async def handle(self, message: str) -> CouncilResponse:
        message = message.strip()
        await self.store.add_chat_message("user", message)
        await self.inventory.emit(
            WallEvent(
                actor="user",
                kind=EventKind.HUMAN_MESSAGE,
                message=message,
                severity="human",
            )
        )

        lower = message.lower()
        contextual_workers = self._contextual_workers(lower)
        if lower.startswith("remember "):
            response = await self._remember(message[9:].strip())
        elif lower.startswith("correct "):
            response = await self._correct(message[8:].strip())
        elif lower.startswith("forget "):
            response = await self._forget(message[7:].strip())
        elif "what" in lower and ("remember" in lower or "learn" in lower):
            response = await self._memories(self._topic_after_about(message))
        elif contextual_workers and any(
            phrase in lower
            for phrase in ("what", "status", "happening", "going on", "doing", "see", "show", "tell")
        ):
            response = self._summarize_workers(contextual_workers)
        elif "awaiting" in lower or "need input" in lower or "blocked" in lower:
            response = self._workers_by_state(
                {WorkerState.AWAITING_INPUT, WorkerState.STALLED, WorkerState.FAILED}
            )
        elif lower.startswith("inspect ") or "what is this worker" in lower:
            response = self._inspect(message.split(maxsplit=1)[-1])
        elif "status" in lower or "what is going on" in lower:
            response = self._status()
        else:
            response = CouncilResponse(
                "Instruction acknowledged and recorded on the wall. The MVP is "
                "observation-first, so no worker pane input was sent. Ask `status`, "
                "`what is blocked?`, `inspect <worker-id>`, `remember <fact>`, or "
                "`what do you remember about <topic>?`.",
                [],
            )

        await self.store.add_chat_message("council", response.message)
        await self.inventory.emit(
            WallEvent(
                actor="council",
                kind=EventKind.COUNCIL_MESSAGE,
                message=response.message,
                data={"worker_ids": response.worker_ids},
            )
        )
        return response

    async def _remember(self, claim: str) -> CouncilResponse:
        scope = "global"
        scope_match = re.match(r"\[(?P<scope>[^]]+)]\s*(?P<claim>.+)", claim)
        if scope_match:
            scope = scope_match.group("scope")
            claim = scope_match.group("claim")
        memory = Memory(
            scope=scope,
            kind=MemoryKind.PREFERENCE,
            claim=claim,
            source="explicit user instruction",
        )
        await self.store.add_memory(memory)
        await self.inventory.emit(
            WallEvent(
                actor="memory-curator",
                kind=EventKind.MEMORY,
                message=f"Remembered [{scope}] {claim}",
                data={"memory_id": memory.memory_id, "scope": scope},
            )
        )
        return CouncilResponse(
            f"Remembered as `{memory.memory_id[:8]}` in scope `{scope}`: {claim}", []
        )

    async def _forget(self, query: str) -> CouncilResponse:
        memories = await self.store.list_memories(query=query)
        if not memories:
            memories = [
                memory
                for memory in await self.store.list_memories()
                if memory.memory_id.startswith(query)
            ]
        if len(memories) != 1:
            if not memories:
                return CouncilResponse(f"No active memory matched `{query}`.", [])
            listing = "\n".join(
                f"- `{item.memory_id[:8]}` [{item.scope}] {item.claim}" for item in memories[:10]
            )
            return CouncilResponse(
                "More than one memory matched; use its ID prefix:\n" + listing, []
            )
        memory = memories[0]
        await self.store.forget_memory(memory.memory_id)
        await self.inventory.emit(
            WallEvent(
                actor="memory-curator",
                kind=EventKind.MEMORY,
                message=f"Forgot [{memory.scope}] {memory.claim}",
                data={"memory_id": memory.memory_id},
            )
        )
        return CouncilResponse(f"Forgot `{memory.memory_id[:8]}`: {memory.claim}", [])

    async def _correct(self, instruction: str) -> CouncilResponse:
        match = re.match(r"(?P<id>[0-9a-f]+)\s+to\s+(?P<claim>.+)", instruction, re.I)
        if not match:
            return CouncilResponse("Use `correct <memory-id> to <new claim>`.", [])
        matches = await self.store.get_memory(match.group("id"))
        if len(matches) != 1:
            return CouncilResponse("No unique active memory matched that ID.", [])
        memory = matches[0]
        old_claim = memory.claim
        memory.claim = match.group("claim").strip()
        memory.source = "explicit user correction"
        await self.store.add_memory(memory)
        await self.inventory.emit(
            WallEvent(
                actor="memory-curator",
                kind=EventKind.MEMORY,
                message=f"Corrected [{memory.scope}] {old_claim} → {memory.claim}",
                data={"memory_id": memory.memory_id},
            )
        )
        return CouncilResponse(
            f"Corrected `{memory.memory_id[:8]}`: {old_claim} → {memory.claim}", []
        )

    async def _memories(self, topic: str | None) -> CouncilResponse:
        memories = await self.store.list_memories(query=topic)
        if not memories:
            suffix = f" about `{topic}`" if topic else ""
            return CouncilResponse(f"No active shared memories{suffix}.", [])
        listing = "\n".join(
            f"- `{item.memory_id[:8]}` [{item.scope}/{item.kind}] {item.claim}"
            for item in memories[:20]
        )
        return CouncilResponse("Active shared memories:\n" + listing, [])

    def _workers_by_state(self, states: set[WorkerState]) -> CouncilResponse:
        workers = [worker for worker in self.inventory.workers.values() if worker.state in states]
        if not workers:
            return CouncilResponse("Observed: no workers are blocked or awaiting input.", [])
        lines = ["Observed workers needing attention:"]
        for worker in sorted(workers, key=lambda item: item.observation.display_name):
            lines.append(
                f"- `{worker.worker_id}` {worker.observation.host}/"
                f"{worker.observation.display_name}: {worker.state.value}; "
                f"{worker.evidence[0] if worker.evidence else 'no evidence recorded'}"
            )
        return CouncilResponse("\n".join(lines), [worker.worker_id for worker in workers])

    def _status(self) -> CouncilResponse:
        workers = list(self.inventory.workers.values())
        counts = {state: 0 for state in WorkerState}
        for worker in workers:
            counts[worker.state] += 1
        important = ", ".join(
            f"{counts[state]} {state.value}"
            for state in WorkerState
            if counts[state]
        ) or "no workers"
        hosts = len({worker.observation.host for worker in workers})
        return CouncilResponse(
            f"Observed: {len(workers)} agent workers across {hosts} hosts ({important}). "
            f"Host connection errors: {len(self.inventory.host_errors)}.",
            [worker.worker_id for worker in workers],
        )

    def _contextual_workers(self, message: str) -> list[Worker]:
        stopwords = {
            "what", "where", "when", "which", "going", "happening", "session",
            "window", "worker", "agent", "status", "about", "with", "that", "this",
        }
        tokens = {
            token
            for token in re.findall(r"[a-z0-9_.-]+", message)
            if len(token) >= 4 and token not in stopwords
        }
        if not tokens:
            return []
        matches: list[Worker] = []
        for worker in self.inventory.workers.values():
            haystack = " ".join(
                (
                    worker.worker_id,
                    worker.observation.host,
                    worker.observation.session_name,
                    worker.observation.window_name,
                    worker.observation.display_name,
                    worker.project or "",
                    worker.purpose,
                )
            ).lower()
            if any(token in haystack for token in tokens):
                matches.append(worker)
        return matches

    @staticmethod
    def _summarize_workers(workers: list[Worker]) -> CouncilResponse:
        lines = [f"Observed {len(workers)} matching worker(s):"]
        for worker in sorted(
            workers, key=lambda item: (item.observation.host, item.observation.display_name)
        ):
            evidence = worker.evidence[0] if worker.evidence else "no evidence recorded"
            lines.append(
                f"- `{worker.worker_id}` {worker.observation.host}/"
                f"{worker.observation.display_name}: {worker.state.value}; "
                f"{worker.purpose}. Evidence: {evidence}"
            )
        return CouncilResponse("\n".join(lines), [worker.worker_id for worker in workers])

    def _inspect(self, query: str) -> CouncilResponse:
        query = query.strip().lower()
        matches = [
            worker
            for worker in self.inventory.workers.values()
            if worker.worker_id.startswith(query)
            or query in worker.observation.display_name.lower()
            or query in worker.purpose.lower()
        ]
        if len(matches) != 1:
            return CouncilResponse(
                "No unique worker matched that identifier. Select a table row or use a "
                "full worker ID.",
                [worker.worker_id for worker in matches],
            )
        worker = matches[0]
        evidence = "; ".join(worker.evidence) or "none"
        return CouncilResponse(
            "Observed:\n"
            f"- Worker: `{worker.worker_id}`\n"
            f"- Location: {worker.observation.host}/{worker.observation.display_name}\n"
            f"- Harness/model: {worker.harness}/{worker.model}\n"
            f"- State: {worker.state.value}\n"
            f"Interpretation: {worker.purpose} (confidence {worker.confidence:.0%}).\n"
            f"Evidence: {evidence}",
            [worker.worker_id],
        )

    @staticmethod
    def _topic_after_about(message: str) -> str | None:
        match = re.search(r"\babout\s+(.+?)[?.]*$", message, re.I)
        return match.group(1).strip() if match else None
