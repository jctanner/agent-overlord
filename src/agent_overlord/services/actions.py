from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from agent_overlord.domain.events import EventKind, WallEvent
from agent_overlord.domain.prompts import (
    ApprovalPolicy,
    PolicyStatus,
    PromptDecision,
    PromptRequest,
    PromptStatus,
)
from agent_overlord.services.classifier import WorkerClassifier
from agent_overlord.services.inventory import InventoryService
from agent_overlord.services.prompts import PromptExtractor
from agent_overlord.storage.sqlite import SQLiteStore


class ActionRejected(RuntimeError):
    pass


class PromptActionArbiter:
    """The only service authorized to translate a prompt decision into tmux input."""

    def __init__(self, store: SQLiteStore, inventory: InventoryService) -> None:
        self.store = store
        self.inventory = inventory
        self._pane_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def decide(
        self,
        prompt_id: str,
        decision: PromptDecision,
        choice: str | None,
        *,
        source: str,
        rationale: str,
        expected_fingerprint: str,
        expected_worker_id: str,
        expected_pane_id: str,
    ) -> PromptRequest:
        prompt = await self.store.get_prompt(prompt_id)
        if prompt is None:
            raise ActionRejected("prompt not found")
        if prompt.status in {
            PromptStatus.SUCCEEDED, PromptStatus.REJECTED, PromptStatus.STALE,
            PromptStatus.FAILED, PromptStatus.EXPIRED,
        }:
            raise ActionRejected(f"prompt is already {prompt.status}")
        if prompt.observation_fingerprint != expected_fingerprint:
            raise ActionRejected("decision fingerprint does not match prompt")
        if prompt.worker_id != expected_worker_id or prompt.pane_id != expected_pane_id:
            raise ActionRejected("decision worker or pane identity does not match prompt")
        prompt.decision = decision
        prompt.selected_choice = choice
        prompt.decision_source = source
        prompt.rationale = rationale
        prompt.decided_at = prompt.updated_at = datetime.now(UTC)
        if decision == PromptDecision.DENY and choice is None:
            choice = "deny"
            prompt.selected_choice = choice
        if decision == PromptDecision.ESCALATE:
            prompt.status = PromptStatus.ESCALATED
        else:
            prompt.status = PromptStatus.DECIDED
        await self.store.save_prompt(prompt)
        await self._emit(prompt, f"Prompt decision recorded: {decision} by {source}")
        return prompt

    async def execute(self, prompt_id: str) -> PromptRequest:
        prompt = await self.store.get_prompt(prompt_id)
        if prompt is None:
            raise ActionRejected("prompt not found")
        lock_key = f"{prompt.host}:{prompt.tmux_socket}:{prompt.pane_id}"
        async with self._pane_locks[lock_key]:
            try:
                return await self._execute_locked(prompt)
            except ActionRejected as exc:
                await self._emit(prompt, f"Action rejected: {exc}", "warning")
                raise

    async def execute_eligible_policy_decisions(self) -> None:
        settings = await self.store.get_automation_settings()
        if (
            not settings.automation_enabled or settings.dry_run or settings.paused
        ):
            return
        for prompt in await self.store.list_prompts(
            status=PromptStatus.DECIDED, limit=200
        ):
            if prompt.decision_source not in {
                "policy", "session_auto_yes", "worker_auto_yes",
                "review_precedent", "fast", "council",
            }:
                continue
            if (
                prompt.decision_source in {"fast", "council"}
                and prompt.worker_id not in settings.auto_yes_workers
            ):
                continue
            if prompt.host in settings.disabled_hosts:
                continue
            if prompt.project and prompt.project in settings.disabled_projects:
                continue
            if prompt.session_id in settings.disabled_sessions:
                continue
            if prompt.worker_id in settings.disabled_workers:
                continue
            try:
                await self.execute(prompt.prompt_id)
            except ActionRejected:
                continue

    async def _execute_locked(self, prompt: PromptRequest) -> PromptRequest:
        settings = await self.store.get_automation_settings()
        if settings.paused:
            raise ActionRejected("automation is globally paused")
        if settings.dry_run:
            raise ActionRejected("dry-run mode prevents pane input")
        if prompt.host in settings.disabled_hosts:
            raise ActionRejected(f"automation is disabled for host {prompt.host}")
        if prompt.project and prompt.project in settings.disabled_projects:
            raise ActionRejected(f"automation is disabled for project {prompt.project}")
        if prompt.session_id in settings.disabled_sessions:
            raise ActionRejected(f"automation is disabled for session {prompt.session_id}")
        if prompt.worker_id in settings.disabled_workers:
            raise ActionRejected(f"automation is disabled for worker {prompt.worker_id}")
        if prompt.status != PromptStatus.DECIDED:
            raise ActionRejected(f"prompt is not decided: {prompt.status}")
        if prompt.decision not in {PromptDecision.ALLOW, PromptDecision.DENY}:
            raise ActionRejected("prompt has no executable allow/deny decision")
        choice = next(
            (item for item in prompt.choices if item.choice == prompt.selected_choice),
            None,
        )
        if choice is None:
            raise ActionRejected("selected choice is not present in the captured prompt")
        auto_yes_source = prompt.decision_source in {
            "worker_auto_yes", "review_precedent", "fast", "council",
        } and prompt.worker_id in settings.auto_yes_workers
        rate_limit = (
            settings.auto_yes_max_actions_per_worker_per_hour
            if auto_yes_source else settings.max_actions_per_pane_per_hour
        )
        await self._check_rate_limit(prompt, rate_limit)
        if prompt.error and prompt.error.startswith("rate limited until "):
            prompt.error = None

        worker = self.inventory.workers.get(prompt.worker_id)
        if worker is None:
            return await self._stale(prompt, "worker no longer exists")
        inventory_prompt = PromptExtractor.extract(worker)
        if inventory_prompt is None or inventory_prompt.prompt_signature != prompt.prompt_signature:
            return await self._stale(prompt, "inventory prompt changed before action")
        transport = self.inventory.discovery.transports.get(prompt.host)
        if transport is None:
            raise ActionRejected(f"no configured transport for host {prompt.host}")

        content = await self._capture(transport, prompt.pane_id)
        recaptured = worker.observation.model_copy(update={"content": content}, deep=True)
        recaptured_worker = WorkerClassifier(self.inventory.config).classify(
            recaptured, worker
        )
        current_prompt = PromptExtractor.extract(recaptured_worker)
        if current_prompt is None or current_prompt.prompt_signature != prompt.prompt_signature:
            return await self._stale(prompt, "current prompt no longer matches decision")

        prompt.status = PromptStatus.EXECUTING
        prompt.pre_action_fingerprint = recaptured.content_fingerprint
        prompt.updated_at = datetime.now(UTC)
        await self.store.save_prompt(prompt)
        await self._emit(prompt, f"Sending bounded choice {choice.choice} to {prompt.pane_id}")

        if choice.response == "enter":
            await transport.run_tmux("send-keys", "-t", prompt.pane_id, "Enter")
        else:
            await transport.run_tmux(
                "send-keys", "-t", prompt.pane_id, "-l", choice.response
            )
            await transport.run_tmux("send-keys", "-t", prompt.pane_id, "Enter")
        prompt.executed_at = datetime.now(UTC)

        deadline = asyncio.get_running_loop().time() + settings.verification_timeout_secs
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.25)
            after = await self._capture(transport, prompt.pane_id)
            after_observation = recaptured.model_copy(update={"content": after}, deep=True)
            if after_observation.content_fingerprint != prompt.pre_action_fingerprint:
                after_worker = WorkerClassifier(self.inventory.config).classify(
                    after_observation, worker
                )
                remaining = PromptExtractor.extract(after_worker)
                if remaining and remaining.prompt_signature == prompt.prompt_signature:
                    continue
                prompt.status = PromptStatus.SUCCEEDED
                prompt.post_action_fingerprint = after_observation.content_fingerprint
                prompt.completed_at = prompt.updated_at = datetime.now(UTC)
                await self.store.save_prompt(prompt)
                await self._record_policy_outcome(prompt, succeeded=True)
                await self._maybe_propose_policy(prompt)
                await self._emit(prompt, "Prompt response verified by changed pane output")
                return prompt

        prompt.status = PromptStatus.FAILED
        prompt.error = "pane did not change before verification deadline"
        prompt.completed_at = prompt.updated_at = datetime.now(UTC)
        await self.store.save_prompt(prompt)
        await self._record_policy_outcome(prompt, succeeded=False)
        await self._emit(prompt, prompt.error, "error")
        return prompt

    async def _capture(self, transport, pane_id: str) -> list[str]:
        output = await transport.run_tmux(
            "capture-pane", "-p", "-S", f"-{self.inventory.config.capture_lines}",
            "-t", pane_id,
        )
        return [line[-4096:] for line in output.rstrip().splitlines()]

    async def _stale(self, prompt: PromptRequest, reason: str) -> PromptRequest:
        prompt.status = PromptStatus.STALE
        prompt.error = reason
        prompt.completed_at = prompt.updated_at = datetime.now(UTC)
        await self.store.save_prompt(prompt)
        await self._record_policy_outcome(prompt, succeeded=False)
        await self._emit(prompt, reason, "warning")
        return prompt

    async def _check_rate_limit(self, prompt: PromptRequest, limit: int) -> None:
        cutoff = datetime.now(UTC) - timedelta(hours=1)
        recent = [
            item for item in await self.store.list_prompts(limit=2000)
            if item.host == prompt.host and item.pane_id == prompt.pane_id
            and item.status == PromptStatus.SUCCEEDED
            and item.completed_at and item.completed_at >= cutoff
        ]
        if len(recent) >= limit:
            retry_at = min(item.completed_at for item in recent) + timedelta(hours=1)
            prompt.error = f"rate limited until {retry_at.isoformat()}"
            prompt.updated_at = datetime.now(UTC)
            await self.store.save_prompt(prompt)
            raise ActionRejected(prompt.error)

    async def _record_policy_outcome(
        self, prompt: PromptRequest, *, succeeded: bool
    ) -> None:
        if not prompt.policy_id:
            return
        policy = await self.store.get_policy(prompt.policy_id)
        if policy is None:
            return
        policy.usage_count += 1
        policy.last_used_at = policy.updated_at = datetime.now(UTC)
        if not succeeded:
            policy.failure_count += 1
            policy.status = PolicyStatus.SUSPENDED
        await self.store.save_policy(policy)

    async def _maybe_propose_policy(self, prompt: PromptRequest) -> None:
        precedent_sources = {
            "human", "fast", "council", "review_precedent",
        }
        if (
            prompt.decision_source not in precedent_sources
            or not prompt.normalized_argv
        ):
            return
        matching_outcomes = [
            item for item in await self.store.list_prompts(limit=2000)
            if item.status == PromptStatus.SUCCEEDED
            and item.decision_source in precedent_sources
            and item.decision == prompt.decision
            and item.selected_choice == prompt.selected_choice
            and item.normalized_argv == prompt.normalized_argv
            and item.host == prompt.host and item.project == prompt.project
        ]
        if len(matching_outcomes) < 3:
            return
        for policy in await self.store.list_policies(include_inactive=True):
            if (
                policy.command_argv == prompt.normalized_argv
                and policy.host == prompt.host and policy.project == prompt.project
            ):
                return
        policy = ApprovalPolicy(
            name=f"Candidate after repeated approvals: {prompt.operation[:100]}",
            status=PolicyStatus.CANDIDATE,
            decision=prompt.decision or PromptDecision.ESCALATE,
            command_argv=prompt.normalized_argv,
            allowed_choices=[prompt.selected_choice or "allow"],
            harness=prompt.harness, host=prompt.host, project=prompt.project,
            risk_ceiling=prompt.risk, created_by="action-arbiter",
            provenance=(
                f"Proposed after {len(matching_outcomes)} verified matching "
                "human/council/precedent outcomes"
            ),
        )
        await self.store.save_policy(policy)
        await self.inventory.emit(
            WallEvent(
                actor="action-arbiter", kind=EventKind.POLICY,
                message=f"Proposed approval policy candidate: {policy.name}",
                host=prompt.host,
                data={"policy_id": policy.policy_id, "prompt_id": prompt.prompt_id},
            )
        )

    async def _emit(
        self, prompt: PromptRequest, message: str, severity: str = "info"
    ) -> None:
        await self.inventory.emit(
            WallEvent(
                actor="action-arbiter",
                kind=EventKind.ACTION,
                message=message,
                worker_id=prompt.worker_id,
                host=prompt.host,
                severity=severity,
                data={"prompt_id": prompt.prompt_id, "status": prompt.status},
            )
        )
