from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
import re

from agent_overlord.domain.council import (
    ActionProposal,
    ControllerRole,
    CouncilNotification,
    EvidenceReference,
    NotificationStatus,
    ProposalCritique,
    ProposalVote,
    SemanticInterpretation,
    VoteValue,
)
from agent_overlord.domain.events import EventKind, WallEvent
from agent_overlord.domain.memories import Memory, MemoryKind, MemoryStatus
from agent_overlord.domain.prompts import PromptDecision, PromptStatus
from agent_overlord.services.control_plane import ControlPlane
from agent_overlord.transports.tmux import TransportError


MAX_FILE_BYTES = 256_000
MAX_FILE_RESULTS = 200


class CouncilToolService:
    """Server-authorized controller operations exposed through MCP."""

    def __init__(
        self, control_plane: ControlPlane, controller_id: str, role: ControllerRole
    ) -> None:
        self.control_plane = control_plane
        self.controller_id = controller_id
        self.role = role

    def list_workers(self, state: str | None = None, host: str | None = None) -> list[dict]:
        workers = self.control_plane.inventory.workers.values()
        return [
            worker.model_dump(mode="json", exclude={"observation": {"content"}})
            for worker in workers
            if (not state or worker.state == state)
            and (not host or worker.observation.host == host)
        ]

    def get_worker(self, worker_id: str) -> dict:
        worker = self._worker(worker_id)
        return worker.model_dump(mode="json", exclude={"observation": {"content"}})

    def get_worker_capture(self, worker_id: str, lines: int = 80) -> dict:
        worker = self._worker(worker_id)
        lines = max(1, min(lines, 500))
        return {
            "worker_id": worker.worker_id,
            "observation_fingerprint": worker.observation.content_fingerprint,
            "observed_at": worker.observation.observed_at.isoformat(),
            "content": worker.observation.content[-lines:],
        }

    async def get_worker_history(self, worker_id: str, limit: int = 50) -> list[dict]:
        self._worker(worker_id)
        return [
            event.model_dump(mode="json")
            for event in await self.control_plane.store.list_events(
                max(1, min(limit, 500)), worker_id
            )
        ]

    async def search_wall(self, query: str = "", limit: int = 100) -> list[dict]:
        events = await self.control_plane.store.list_events(max(1, min(limit, 500)))
        needle = query.casefold().strip()
        return [
            event.model_dump(mode="json")
            for event in events
            if not needle or needle in event.model_dump_json().casefold()
        ]

    async def search_memories(self, query: str = "") -> list[dict]:
        memories = await self.control_plane.store.list_memories(
            query or None, include_inactive=True
        )
        return [memory.model_dump(mode="json") for memory in memories]

    _LEDGER_PATH = Path(__file__).resolve().parents[3] / "docs" / "notes" / "agentic_work_ledger.md"

    def get_agentic_ledger(self) -> str:
        """Return the agentic work ledger specification. Most monitored projects
        follow this methodology for task lifecycle, directory layout, and project
        management. Consult this when evaluating operations involving task files,
        PLAN.md, ADRs, bug reports, or git mv between task directories."""
        try:
            return self._LEDGER_PATH.read_text()
        except FileNotFoundError:
            raise ValueError("agentic work ledger not found")

    async def get_chat_context(self, limit: int = 50) -> list[dict]:
        messages = await self.control_plane.store.list_chat_messages(
            max(1, min(limit, 200))
        )
        return [{"role": role, "message": message} for role, message in messages]

    async def get_host_health(self, host: str) -> dict:
        for item in (await self.control_plane.health()).hosts:
            if item.name == host:
                return item.model_dump(mode="json")
        raise ValueError("host not found")

    async def get_prompt(self, prompt_id: str) -> dict:
        item = await self.control_plane.store.get_prompt(prompt_id)
        if item is None:
            raise ValueError("prompt not found")
        return item.model_dump(mode="json")

    async def review_prompt(
        self,
        notification_id: str,
        prompt_id: str,
        decision: PromptDecision,
        rationale: str,
        choice: str = "",
    ) -> dict:
        notification = await self._notification(notification_id)
        if notification.prompt_id != prompt_id:
            raise ValueError("notification is not assigned to this prompt")
        if notification.status != NotificationStatus.RUNNING:
            raise ValueError("notification is not running")
        if (
            notification.target_controller_ids
            and self.controller_id not in notification.target_controller_ids
        ):
            raise ValueError("controller is not assigned to this prompt review")
        prompt = await self.control_plane.store.get_prompt(prompt_id)
        if prompt is None:
            raise ValueError("prompt not found")
        if prompt.status not in {PromptStatus.EVALUATING, PromptStatus.ESCALATED}:
            raise ValueError("prompt is not awaiting semantic review")
        if choice and choice not in {item.choice for item in prompt.choices}:
            raise ValueError("choice is not present in the captured prompt")
        prompt.review_decisions[self.controller_id] = decision
        prompt.review_rationales[self.controller_id] = rationale
        if choice:
            prompt.review_choices[self.controller_id] = choice
        if self.controller_id not in prompt.reviewer_ids:
            prompt.reviewer_ids.append(self.controller_id)
        prompt.updated_at = datetime.now(UTC)
        await self.control_plane.store.save_prompt(prompt)
        return {"recorded": True, "prompt_id": prompt_id, "decision": decision}

    def get_project_context(self, worker_id: str) -> dict:
        worker = self._worker(worker_id)
        return {
            "worker_id": worker_id,
            "project": worker.project,
            "current_path": worker.observation.current_path,
            "host": worker.observation.host,
            "session": worker.observation.session_name,
            "window": worker.observation.window_name,
            "note": "Use the session directory, file search, and file reading tools for repository context.",
        }

    async def list_session_directory(
        self, worker_id: str, path: str = ".", limit: int = 100
    ) -> dict:
        """List files and directories beneath a worker's current directory."""
        worker, transport, root, target = await self._session_path(worker_id, path)
        limit = max(1, min(limit, MAX_FILE_RESULTS))
        try:
            output = await transport.run_command(
                "find", target, "-mindepth", "1", "-maxdepth", "1",
                "-printf", "%y\t%p\n", timeout=10,
            )
        except TransportError as exc:
            raise ValueError(f"could not list session directory: {exc}") from exc
        entries = []
        for line in output.splitlines()[:limit]:
            kind, separator, name = line.partition("\t")
            if not separator:
                continue
            entries.append({
                "path": self._relative_session_path(root, name),
                "type": {"d": "directory", "f": "file", "l": "symlink"}.get(
                    kind, "other"
                ),
            })
        return {
            "worker_id": worker.worker_id, "path": self._relative_session_path(root, target),
            "entries": entries, "truncated": len(output.splitlines()) > limit,
        }

    async def find_session_files(
        self,
        worker_id: str,
        pattern: str,
        path: str = ".",
        pattern_type: str = "glob",
        max_depth: int = 5,
        limit: int = 100,
    ) -> dict:
        """Find session files by path glob or POSIX extended regular expression."""
        worker, transport, root, target = await self._session_path(worker_id, path)
        if not pattern or "\x00" in pattern:
            raise ValueError("pattern must not be empty or contain NUL")
        if pattern_type not in {"glob", "regex"}:
            raise ValueError("pattern_type must be glob or regex")
        if pattern_type == "regex":
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regular expression: {exc}") from exc
        max_depth = max(1, min(max_depth, 12))
        limit = max(1, min(limit, MAX_FILE_RESULTS))
        command = ["find", target, "-maxdepth", str(max_depth), "-type", "f"]
        if pattern_type == "glob":
            command.extend(("-path", pattern if "/" in pattern else f"*/{pattern}"))
        else:
            command[2:2] = ["-regextype", "posix-extended"]
            command.extend(("-regex", pattern))
        command.append("-print")
        try:
            output = await transport.run_command(*command, timeout=10)
        except TransportError as exc:
            raise ValueError(f"could not search session files: {exc}") from exc
        matches = [
            self._relative_session_path(root, item)
            for item in output.splitlines()[:limit]
        ]
        return {
            "worker_id": worker.worker_id, "path": self._relative_session_path(root, target),
            "pattern": pattern, "pattern_type": pattern_type, "matches": matches,
            "truncated": len(output.splitlines()) > limit,
        }

    async def read_session_file(
        self, worker_id: str, path: str, max_bytes: int = 64_000
    ) -> dict:
        """Read a bounded text file from a worker's local or remote session directory."""
        worker, transport, root, target = await self._session_path(worker_id, path)
        max_bytes = max(1, min(max_bytes, MAX_FILE_BYTES))
        try:
            digest_output = await transport.run_command(
                "sha256sum", "--", target, timeout=10
            )
            output = await transport.run_command(
                "head", "-c", str(max_bytes + 1), "--", target, timeout=10
            )
        except TransportError as exc:
            raise ValueError(f"could not read session file: {exc}") from exc
        encoded = output.encode("utf-8", errors="replace")
        truncated = len(encoded) > max_bytes
        if truncated:
            encoded = encoded[:max_bytes]
            output = encoded.decode("utf-8", errors="replace")
        if "\x00" in output:
            raise ValueError("session file appears to be binary")
        digest = digest_output.split(maxsplit=1)[0] if digest_output else ""
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("could not verify session file digest")
        return {
            "worker_id": worker.worker_id,
            "path": self._relative_session_path(root, target),
            "content": output,
            "sha256": digest,
            "read_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "truncated": truncated,
        }

    async def _session_path(self, worker_id: str, path: str):
        worker = self._worker(worker_id)
        root = worker.observation.current_path.strip()
        if not root or not PurePosixPath(root).is_absolute():
            raise ValueError("worker current directory is unavailable")
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts or "\x00" in path:
            raise ValueError("path must stay within the worker current directory")
        transport = self.control_plane.inventory.discovery.transports.get(
            worker.observation.host
        )
        if transport is None:
            raise ValueError("worker host transport is unavailable")
        candidate = str(PurePosixPath(root).joinpath(relative))
        try:
            resolved_root = (await transport.run_command(
                "realpath", "-e", "--", root, timeout=10
            )).strip()
            resolved_target = (await transport.run_command(
                "realpath", "-e", "--", candidate, timeout=10
            )).strip()
        except TransportError as exc:
            raise ValueError(f"session path does not exist: {path}") from exc
        root_path = PurePosixPath(resolved_root)
        target_path = PurePosixPath(resolved_target)
        if target_path != root_path and root_path not in target_path.parents:
            raise ValueError("resolved path escapes the worker current directory")
        return worker, transport, resolved_root, resolved_target

    @staticmethod
    def _relative_session_path(root: str, path: str) -> str:
        target = PurePosixPath(path)
        base = PurePosixPath(root)
        if target == base:
            return "."
        try:
            return str(target.relative_to(base))
        except ValueError as exc:
            raise ValueError("result path escapes the worker current directory") from exc

    async def propose_memory(
        self,
        claim: str,
        scope: str = "global",
        kind: MemoryKind = MemoryKind.SEMANTIC,
        confidence: float = 0.5,
        evidence: list[str] | None = None,
    ) -> dict:
        item = Memory(
            claim=claim,
            scope=scope,
            kind=kind,
            source=f"controller inference; evidence={evidence or []}",
            created_by=self.controller_id,
            confidence=confidence,
            status=MemoryStatus.CANDIDATE,
        )
        await self.control_plane.store.add_memory(item)
        await self.control_plane.publish_memory("created", item)
        await self.control_plane.inventory.emit(
            WallEvent(
                actor=self.controller_id,
                kind=EventKind.MEMORY,
                message=f"Proposed memory candidate: {claim}",
                data={"memory_id": item.memory_id, "scope": scope, "evidence": evidence or []},
            )
        )
        return item.model_dump(mode="json")

    async def get_interpretations(self, worker_id: str, limit: int = 20) -> list[dict]:
        self._worker(worker_id)
        items = await self.control_plane.store.list_interpretations(worker_id, limit)
        return [item.model_dump(mode="json") for item in items]

    async def record_interpretation(
        self,
        worker_id: str,
        observation_fingerprint: str,
        goal: str = "",
        current_activity: str = "",
        blocker: str = "",
        requested_operation: str = "",
        project_context: str = "",
        completion_criteria: str = "",
        confidence: float = 0.0,
        evidence: list[dict] | None = None,
    ) -> dict:
        worker = self._worker(worker_id)
        if worker.observation.content_fingerprint != observation_fingerprint:
            raise ValueError("observation is stale; recapture the worker before interpreting")
        previous = await self.control_plane.store.list_interpretations(worker_id, 1)
        evidence_items = [
            EvidenceReference.model_validate(value) for value in (evidence or [])
        ]
        if not evidence_items:
            evidence_items.append(
                EvidenceReference(
                    kind="capture",
                    reference=observation_fingerprint,
                    excerpt="\n".join(worker.observation.content[-3:])[-1000:],
                )
            )
        item = SemanticInterpretation(
            worker_id=worker_id,
            observation_fingerprint=observation_fingerprint,
            goal=goal or None,
            current_activity=current_activity or None,
            blocker=blocker or None,
            requested_operation=requested_operation or None,
            project_context=project_context or None,
            completion_criteria=completion_criteria or None,
            confidence=confidence,
            evidence=evidence_items,
            controller_id=self.controller_id,
            version=(previous[0].version + 1) if previous else 1,
        )
        await self.control_plane.store.add_interpretation(item)
        await self.control_plane.inventory.emit(
            WallEvent(
                actor=self.controller_id,
                kind=EventKind.INTERPRETATION,
                worker_id=worker_id,
                host=worker.observation.host,
                message=f"Interpreted {worker.observation.display_name}: {item.goal or item.current_activity or 'insufficient evidence'}",
                data={"interpretation_id": item.interpretation_id, "confidence": confidence},
            )
        )
        return item.model_dump(mode="json")

    async def post_wall_message(
        self, message: str, worker_id: str | None = None, references: list[str] | None = None
    ) -> dict:
        event = WallEvent(
            actor=self.controller_id,
            kind=EventKind.CONTROLLER_MESSAGE,
            message=message,
            worker_id=worker_id,
            data={"role": self.role, "references": references or []},
        )
        await self.control_plane.inventory.emit(event)
        return event.model_dump(mode="json")

    async def submit_proposal(
        self,
        operation: str,
        rationale: str,
        risk: str = "unknown",
        target_worker_id: str | None = None,
        observation_fingerprint: str | None = None,
    ) -> dict:
        if target_worker_id:
            worker = self._worker(target_worker_id)
            if observation_fingerprint != worker.observation.content_fingerprint:
                raise ValueError("proposal must reference the current observation fingerprint")
        item = ActionProposal(
            controller_id=self.controller_id,
            operation=operation,
            target_worker_id=target_worker_id,
            observation_fingerprint=observation_fingerprint,
            rationale=rationale,
            risk=risk,
        )
        await self.control_plane.store.save_proposal(item)
        await self._proposal_event("PROPOSAL", item.proposal_id, operation)
        return item.model_dump(mode="json")

    async def critique_proposal(self, proposal_id: str, findings: str) -> dict:
        if not await self.control_plane.store.get_proposal(proposal_id):
            raise ValueError("proposal not found")
        item = ProposalCritique(
            proposal_id=proposal_id,
            controller_id=self.controller_id,
            findings=findings,
        )
        await self.control_plane.store.add_critique(item)
        await self._proposal_event("CRITIQUE", proposal_id, findings)
        return item.model_dump(mode="json")

    async def vote_on_proposal(
        self, proposal_id: str, vote: VoteValue, rationale: str
    ) -> dict:
        if not await self.control_plane.store.get_proposal(proposal_id):
            raise ValueError("proposal not found")
        item = ProposalVote(
            proposal_id=proposal_id,
            controller_id=self.controller_id,
            vote=vote,
            rationale=rationale,
        )
        await self.control_plane.store.add_vote(item)
        await self._proposal_event("VOTE", proposal_id, f"{vote}: {rationale}")
        return item.model_dump(mode="json")

    async def answer_human_message(
        self, notification_id: str, answer: str, references: list[str] | None = None
    ) -> dict:
        if self.role != ControllerRole.STRATEGIST:
            raise ValueError("only the strategist may propose a human council answer")
        notification = await self._notification(notification_id)
        if not notification.human_message:
            raise ValueError("notification is not a human council question")
        if notification.status != NotificationStatus.RUNNING:
            raise ValueError("notification is not running")
        if notification.answer is not None:
            return {"recorded": False, "reason": "already answered"}
        notification.answer = answer
        notification.answer_references = references or []
        notification.answered_by = self.controller_id
        await self.control_plane.store.save_notification(notification)
        return {"recorded": True, "published": False}

    async def signal_done(self, notification_id: str, summary: str = "") -> dict:
        notification = await self._notification(notification_id)
        notification.summary = summary
        notification.completion_signals[self.controller_id] = summary
        await self.control_plane.store.save_notification(notification)
        return {"acknowledged": True, "notification_id": notification_id}

    def _worker(self, worker_id: str):
        worker = self.control_plane.inventory.workers.get(worker_id)
        if worker is None:
            raise ValueError("worker not found")
        return worker

    async def _notification(self, notification_id: str) -> CouncilNotification:
        item = await self.control_plane.store.get_notification(notification_id)
        if item is None:
            raise ValueError("notification not found")
        return item

    async def _proposal_event(self, action: str, proposal_id: str, message: str) -> None:
        await self.control_plane.inventory.emit(
            WallEvent(
                actor=self.controller_id,
                kind=EventKind.PROPOSAL,
                message=f"{action} {proposal_id[:8]}: {message}",
                data={"proposal_id": proposal_id, "action": action},
            )
        )
