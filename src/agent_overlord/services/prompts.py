from __future__ import annotations

import hashlib
import re
import shlex
from datetime import UTC, datetime, timedelta

from agent_overlord.domain.events import EventKind, WallEvent
from agent_overlord.domain.prompts import (
    ApprovalPolicy,
    AutomationSettings,
    MatchKind,
    PolicyStatus,
    PromptChoice,
    PromptDecision,
    PromptRequest,
    PromptRisk,
    PromptStatus,
    ReviewTier,
)
from agent_overlord.domain.workers import InputKind, Worker
from agent_overlord.domain.council import CouncilNotification, NotificationStatus
from agent_overlord.services.inventory import InventoryService
from agent_overlord.storage.sqlite import SQLiteStore


TERMINAL_STATUSES = {
    PromptStatus.SUCCEEDED,
    PromptStatus.REJECTED,
    PromptStatus.STALE,
    PromptStatus.FAILED,
    PromptStatus.EXPIRED,
}
RISK_ORDER = {
    PromptRisk.ROUTINE: 0,
    PromptRisk.ELEVATED: 1,
    PromptRisk.HIGH: 2,
    PromptRisk.UNKNOWN: 3,
}
HIGH_RISK_WORDS = re.compile(
    r"\b(rm|delete|destroy|deploy|publish|release|push|reset|credential|secret|"
    r"password|sudo|chmod|chown|terraform|kubectl|oc)\b",
    re.IGNORECASE,
)
ELEVATED_WORDS = re.compile(
    r"\b(curl|wget|pip|uv\s+(?:add|sync)|npm\s+(?:install|update)|"
    r"pnpm\s+(?:install|update)|apt|dnf|brew|git\s+commit)\b",
    re.IGNORECASE,
)
ROUTINE_WORDS = re.compile(
    r"\b(pytest|ruff|mypy|eslint|prettier|npm\s+test|cargo\s+test|go\s+test|"
    r"git\s+(?:status|diff|log)|rg|grep|sed|cat|head|tail|find|ls|pwd|wc)\b",
    re.IGNORECASE,
)
BACKTICK_COMMAND = re.compile(r"`([^`\n]{2,2000})`")
SHELL_COMMAND = re.compile(r"^\s*(?:\$|❯|>)\s+(.{2,2000})$")
NUMBERED_CHOICE = re.compile(r"^\s*(?:[❯›>]\s*)?([1-9])[.)]?\s+(.+)$")
SHELL_CONTROL_TOKENS = {"&&", "||", ";", "|", "&", ">", ">>", "<", "<<"}
PROMPT_FOOTER = re.compile(
    r"^(?:(?:esc|tab|ctrl|shift|enter)\b|\d+\s+tasks?\b|[◼◻✔…]\s*)",
    re.IGNORECASE,
)


class PromptExtractor:
    @classmethod
    def extract(cls, worker: Worker) -> PromptRequest | None:
        if not worker.awaiting_input:
            return None
        observation = worker.observation
        recent = [line.rstrip() for line in observation.content[-30:]]
        if not cls._appears_live(recent):
            return None
        evidence = [line for line in recent[-15:] if line.strip()]
        if not evidence:
            return None
        operation = cls._operation(recent)
        choices = cls._choices(recent, worker.input_kind, worker.harness)
        prompt_type = (worker.input_kind or InputKind.UNKNOWN).value
        argv = cls.normalize_argv(operation)
        signature_value = "\n".join(
            [prompt_type, operation, *(f"{item.choice}:{item.response}" for item in choices)]
        )
        signature = hashlib.sha256(signature_value.encode()).hexdigest()
        risk, reasons = cls.risk(operation, argv, prompt_type)
        return PromptRequest(
            worker_id=worker.worker_id,
            host=observation.host,
            tmux_socket=observation.tmux_socket,
            session_id=observation.session_id,
            session_name=observation.session_name,
            window_id=observation.window_id,
            window_name=observation.window_name,
            pane_id=observation.pane_id,
            pane_index=observation.pane_index,
            harness=worker.harness,
            project=worker.project,
            prompt_type=prompt_type,
            operation=operation,
            normalized_argv=argv,
            choices=choices,
            observation_fingerprint=observation.content_fingerprint,
            prompt_signature=signature,
            evidence=evidence,
            confidence=0.95 if operation != "unknown operation" and choices else 0.6,
            risk=risk,
            risk_reasons=reasons,
            tier=cls.default_tier(risk),
        )

    @staticmethod
    def _appears_live(lines: list[str]) -> bool:
        nonempty = [(index, line.strip()) for index, line in enumerate(lines) if line.strip()]
        if not nonempty:
            return False
        prompt_indexes = [
            index for index, line in nonempty
            if (
                NUMBERED_CHOICE.match(line)
                and not re.match(r"^\d+\s+tasks?\b", line, re.IGNORECASE)
            )
            or re.search(
                r"allow|approve|permission|yes/no|\(y/n\)|press enter|\?\s*$",
                line,
                re.IGNORECASE,
            )
        ]
        if not prompt_indexes:
            return False
        last_prompt = max(prompt_indexes)
        trailing = [line for index, line in nonempty if index > last_prompt]
        # Claude keeps its task/progress footer below a modal prompt. Those
        # lines are interface chrome, not evidence that the prompt transcript
        # is stale. Genuine tool or assistant output remains disallowed.
        return all(PROMPT_FOOTER.search(line) is not None for line in trailing)

    @staticmethod
    def normalize_argv(operation: str) -> list[str]:
        if operation == "unknown operation":
            return []
        try:
            return shlex.split(operation, posix=True)
        except ValueError:
            return []

    @staticmethod
    def risk(
        operation: str, argv: list[str], prompt_type: str
    ) -> tuple[PromptRisk, list[str]]:
        if prompt_type in {InputKind.CREDENTIAL.value}:
            return PromptRisk.HIGH, ["credential or authentication input"]
        if operation == "unknown operation" or not argv:
            return PromptRisk.UNKNOWN, ["operation could not be parsed structurally"]
        if HIGH_RISK_WORDS.search(operation):
            return PromptRisk.HIGH, ["operation matches a high-risk action category"]
        if ELEVATED_WORDS.search(operation):
            return PromptRisk.ELEVATED, ["operation may change dependencies or external state"]
        if ROUTINE_WORDS.search(operation):
            return PromptRisk.ROUTINE, ["operation matches a routine development command"]
        return PromptRisk.UNKNOWN, ["operation has no established risk classification"]

    @staticmethod
    def default_tier(risk: PromptRisk) -> ReviewTier:
        if risk == PromptRisk.ROUTINE:
            return ReviewTier.HUMAN
        if risk == PromptRisk.ELEVATED:
            return ReviewTier.FAST
        return ReviewTier.COUNCIL if risk == PromptRisk.HIGH else ReviewTier.HUMAN

    @staticmethod
    def _operation(lines: list[str]) -> str:
        # Claude renders a command as an indented block followed by one
        # human-readable description line. Long commands wrap visually in
        # tmux, so reconstruct the block before attempting argv parsing.
        for index, line in enumerate(lines):
            if not re.match(
                r"^\s*(?:bash\s+)?(?:command|operation)\s*:?\s*$", line, re.I
            ):
                continue
            block: list[str] = []
            started = False
            for candidate in lines[index + 1:]:
                stripped = candidate.strip()
                if not stripped:
                    if started:
                        break
                    continue
                started = True
                block.append(stripped)
            if len(block) >= 2:
                # The final line is Claude's description (for example
                # "Run all marker selections"), not part of the command.
                return " ".join(block[:-1])
            if block:
                return block[0]
        for line in reversed(lines):
            for match in BACKTICK_COMMAND.findall(line):
                if match.strip():
                    return match.strip()
            match = SHELL_COMMAND.match(line)
            if (
                match
                and not NUMBERED_CHOICE.match(line)
                and not re.match(r"^(yes|no|allow|deny|\d+)$", match.group(1), re.I)
            ):
                return match.group(1).strip()
        return "unknown operation"

    @staticmethod
    def _choices(
        lines: list[str], input_kind: InputKind | None, harness: str
    ) -> list[PromptChoice]:
        if harness.startswith("claude"):
            parsed = PromptExtractor._numbered_choices(lines)
            if parsed:
                return parsed
        elif harness == "codex":
            text = "\n".join(lines[-15:])
            if re.search(r"\byes/no\b|\(y/n\)", text, re.I):
                return [
                    PromptChoice(choice="allow", label="Yes", response="y"),
                    PromptChoice(choice="deny", label="No", response="n"),
                ]
        # Unknown and evolving harness formats use conservative shared parsing.
        choices = PromptExtractor._numbered_choices(lines)
        if choices:
            return choices
        text = "\n".join(lines[-15:])
        if re.search(r"\byes/no\b|\(y/n\)", text, re.I):
            return [
                PromptChoice(choice="allow", label="Yes", response="y"),
                PromptChoice(choice="deny", label="No", response="n"),
            ]
        if input_kind == InputKind.CONFIRMATION:
            return [PromptChoice(choice="allow", label="Press Enter", response="enter")]
        return []

    @staticmethod
    def _numbered_choices(lines: list[str]) -> list[PromptChoice]:
        choices: list[PromptChoice] = []
        for line in lines[-15:]:
            if re.match(r"^\s*\d+\s+tasks?\b", line, re.IGNORECASE):
                continue
            match = NUMBERED_CHOICE.match(line)
            if not match:
                continue
            number, label = match.groups()
            lowered = label.casefold()
            semantic = (
                "deny" if any(word in lowered for word in ("no", "deny", "reject"))
                else "allow_always" if "don't ask" in lowered or "always" in lowered
                else "allow" if any(word in lowered for word in ("yes", "allow", "approve"))
                else f"select_{number}"
            )
            choices.append(PromptChoice(choice=semantic, label=label.strip(), response=number))
        if choices:
            return choices
        return []


class PromptService:
    def __init__(self, store: SQLiteStore, inventory: InventoryService) -> None:
        self.store = store
        self.inventory = inventory
        self.action_arbiter = None
        self.review_scheduler = None

    async def initialize(self) -> None:
        now = datetime.now(UTC)
        for prompt in await self.store.list_prompts(limit=2000):
            if prompt.status == PromptStatus.EXECUTING:
                prompt.status = PromptStatus.FAILED
                prompt.error = "action outcome was unknown after service restart; not repeated"
                prompt.completed_at = prompt.updated_at = now
                await self.store.save_prompt(prompt)
            elif prompt.status == PromptStatus.EVALUATING:
                prompt.status = PromptStatus.ESCALATED
                prompt.tier = ReviewTier.HUMAN
                prompt.error = "semantic review was interrupted by service restart"
                prompt.updated_at = now
                await self.store.save_prompt(prompt)

    async def observe_workers(self, workers: list[Worker]) -> None:
        current = {worker.worker_id: worker for worker in workers}
        now = datetime.now(UTC)
        settings = await self.store.get_automation_settings()
        active = [
            item for item in await self.store.list_prompts(limit=1000)
            if item.status not in TERMINAL_STATUSES
        ]
        for item in active:
            worker = current.get(item.worker_id)
            if worker is not None and worker.awaiting_input:
                extracted = PromptExtractor.extract(worker)
                if (
                    extracted is not None
                    and extracted.prompt_signature == item.prompt_signature
                ):
                    if item.observation_fingerprint != worker.observation.content_fingerprint:
                        item.observation_fingerprint = worker.observation.content_fingerprint
                        item.updated_at = now
                        await self.store.save_prompt(item)
                    continue
            item.status = PromptStatus.STALE
            if worker is None or not worker.awaiting_input:
                item.error = "worker prompt changed or disappeared"
            else:
                item.error = "prompt replaced by a different operation"
            item.completed_at = item.updated_at = now
            await self.store.save_prompt(item)
            await self._emit(item, item.error, "warning")

        for worker in workers:
            extracted = PromptExtractor.extract(worker)
            if extracted is None:
                continue
            existing = await self.store.get_prompt_for_observation(
                worker.worker_id, extracted.observation_fingerprint
            )
            if (
                existing is not None
                and existing.status == PromptStatus.ESCALATED
                and existing.worker_id in settings.auto_yes_workers
                and self.review_scheduler is not None
                and await self._was_failed_fast_review(existing)
            ):
                await self._emit(
                    existing,
                    "Recovering failed fast review by promoting to full council",
                    "warning",
                )
                await self.request_review(
                    existing.prompt_id, ReviewTier.COUNCIL, self.review_scheduler
                )
                continue
            if (
                existing is not None
                and existing.prompt_signature == extracted.prompt_signature
                and existing.status != PromptStatus.EXPIRED
            ):
                continue
            if existing is not None:
                # A parser improvement can produce a better structured prompt
                # for an unchanged screen. Reuse the durable row (which is
                # unique by worker/fingerprint) and restart its lifetime.
                extracted = extracted.model_copy(
                    update={"prompt_id": existing.prompt_id}
                )
            policy = await self.match_policy(extracted)
            if policy:
                extracted.tier = ReviewTier.POLICY
                extracted.decision = policy.decision
                extracted.decision_source = "policy"
                extracted.policy_id = policy.policy_id
                extracted.rationale = policy.provenance
                if policy.decision == PromptDecision.ESCALATE:
                    extracted.status = PromptStatus.ESCALATED
                    extracted.tier = ReviewTier.HUMAN
                else:
                    extracted.status = PromptStatus.DECIDED
                    extracted.selected_choice = policy.allowed_choices[0]
                    extracted.decided_at = datetime.now(UTC)
                extracted.updated_at = datetime.now(UTC)
            elif precedent := await self.match_review_precedent(extracted, settings):
                extracted.tier = ReviewTier.POLICY
                extracted.status = PromptStatus.DECIDED
                extracted.decision = PromptDecision.ALLOW
                extracted.selected_choice = precedent.selected_choice
                extracted.decision_source = "review_precedent"
                extracted.rationale = (
                    "exact verified council precedent from prompt "
                    f"{precedent.prompt_id}"
                )
                extracted.decided_at = extracted.updated_at = datetime.now(UTC)
            elif self._worker_auto_yes(extracted, settings):
                allow = next(
                    (choice for choice in extracted.choices if choice.choice == "allow"),
                    None,
                )
                if allow is not None:
                    extracted.tier = ReviewTier.POLICY
                    extracted.decision = PromptDecision.ALLOW
                    extracted.decision_source = "worker_auto_yes"
                    extracted.rationale = (
                        "explicit worker-pane Auto yes grant; "
                        f"risk classified as {extracted.risk}"
                    )
                    extracted.status = PromptStatus.DECIDED
                    extracted.selected_choice = allow.choice
                    extracted.decided_at = extracted.updated_at = datetime.now(UTC)
            await self.store.save_prompt(extracted)
            await self._emit(
                extracted,
                f"Detected {extracted.prompt_type} prompt for {worker.observation.display_name}",
            )
            if (
                extracted.risk == PromptRisk.UNKNOWN
                and extracted.worker_id in settings.auto_yes_workers
                and self.review_scheduler is not None
                and extracted.status == PromptStatus.DETECTED
            ):
                await self.request_review(
                    extracted.prompt_id, ReviewTier.FAST, self.review_scheduler
                )
        if self.action_arbiter is not None:
            await self.action_arbiter.execute_eligible_policy_decisions()

    async def _was_failed_fast_review(self, prompt: PromptRequest) -> bool:
        if not prompt.review_notification_id:
            return False
        notification = await self.store.get_notification(prompt.review_notification_id)
        return bool(
            notification
            and notification.reason == "prompt_review_fast"
            and notification.status != NotificationStatus.COMPLETED
        )

    async def apply_auto_yes_settings(self, settings: AutomationSettings) -> None:
        """Apply a newly granted session scope to prompts already on screen."""
        now = datetime.now(UTC)
        for prompt in await self.store.list_prompts(limit=1000):
            if prompt.status not in {
                PromptStatus.DETECTED, PromptStatus.ESCALATED,
            } or not self._worker_auto_yes(prompt, settings):
                continue
            allow = next(
                (choice for choice in prompt.choices if choice.choice == "allow"), None
            )
            if allow is None:
                continue
            prompt.tier = ReviewTier.POLICY
            prompt.status = PromptStatus.DECIDED
            prompt.decision = PromptDecision.ALLOW
            prompt.selected_choice = allow.choice
            prompt.decision_source = "worker_auto_yes"
            prompt.rationale = (
                "explicit worker-pane Auto yes grant; "
                f"risk classified as {prompt.risk}"
            )
            prompt.decided_at = prompt.updated_at = now
            await self.store.save_prompt(prompt)
            await self._emit(prompt, "Worker-pane Auto yes decision recorded")
        if self.action_arbiter is not None:
            await self.action_arbiter.execute_eligible_policy_decisions()

    @staticmethod
    def _worker_auto_yes(prompt: PromptRequest, settings: AutomationSettings) -> bool:
        return (
            prompt.worker_id in settings.auto_yes_workers
            and prompt.risk in {PromptRisk.ROUTINE, PromptRisk.ELEVATED}
        )

    async def match_review_precedent(
        self, prompt: PromptRequest, settings: AutomationSettings
    ) -> PromptRequest | None:
        """Return an exact, recent, verified semantic-review precedent."""
        if (
            prompt.worker_id not in settings.auto_yes_workers
            or prompt.risk == PromptRisk.HIGH
            or not prompt.normalized_argv
        ):
            return None
        current_choices = {choice.choice for choice in prompt.choices}
        cutoff = datetime.now(UTC) - timedelta(
            seconds=settings.review_precedent_ttl_secs
        )
        for prior in await self.store.list_prompts(limit=2000):
            verified_at = prior.completed_at or prior.updated_at
            if verified_at < cutoff:
                continue
            if (
                prior.status == PromptStatus.SUCCEEDED
                and prior.decision == PromptDecision.ALLOW
                and prior.decision_source in {
                    ReviewTier.FAST.value, ReviewTier.COUNCIL.value
                }
                and prior.selected_choice in current_choices
                and prior.normalized_argv == prompt.normalized_argv
                and prior.project == prompt.project
                and prior.host == prompt.host
                and prior.harness == prompt.harness
                and prior.prompt_type == prompt.prompt_type
                and prior.risk == prompt.risk
                and prior.pre_action_fingerprint
                and prior.post_action_fingerprint
                and prior.pre_action_fingerprint != prior.post_action_fingerprint
            ):
                return prior
        return None

    async def match_policy(self, prompt: PromptRequest) -> ApprovalPolicy | None:
        # Tier 0 is deliberately limited to routine operations. Elevated,
        # high-risk, credential, and unknown prompts always require review.
        if prompt.risk != PromptRisk.ROUTINE:
            return None
        now = datetime.now(UTC)
        for policy in await self.store.list_policies():
            if policy.expires_at and policy.expires_at <= now:
                policy.status = PolicyStatus.EXPIRED
                policy.updated_at = now
                await self.store.save_policy(policy)
                continue
            if RISK_ORDER[prompt.risk] > RISK_ORDER[policy.risk_ceiling]:
                continue
            if policy.harness and policy.harness != prompt.harness:
                continue
            if policy.host and policy.host != prompt.host:
                continue
            if policy.project and policy.project != prompt.project:
                continue
            if policy.worker_id and policy.worker_id != prompt.worker_id:
                continue
            if policy.session_id and policy.session_id != prompt.session_id:
                continue
            if policy.match_kind == MatchKind.EXACT:
                matched = prompt.normalized_argv == policy.command_argv
            else:
                remainder = prompt.normalized_argv[len(policy.command_argv):]
                if any(
                    token in SHELL_CONTROL_TOKENS
                    or "$(" in token or "`" in token
                    for token in remainder
                ):
                    continue
                matched = (
                    len(prompt.normalized_argv) >= len(policy.command_argv)
                    and prompt.normalized_argv[:len(policy.command_argv)] == policy.command_argv
                )
            if matched:
                prompt_choices = {item.choice for item in prompt.choices}
                if (
                    policy.decision != PromptDecision.ESCALATE
                    and (
                        not policy.allowed_choices
                        or any(
                            choice not in prompt_choices
                            for choice in policy.allowed_choices
                        )
                    )
                ):
                    continue
                return policy
        return None

    async def request_review(self, prompt_id: str, tier: ReviewTier, scheduler):
        if tier not in {ReviewTier.FAST, ReviewTier.COUNCIL}:
            raise ValueError("semantic review tier must be fast or council")
        prompt = await self.store.get_prompt(prompt_id)
        if prompt is None:
            raise ValueError("prompt not found")
        if prompt.status in TERMINAL_STATUSES:
            raise ValueError(f"prompt is already {prompt.status}")
        worker = self.inventory.workers.get(prompt.worker_id)
        if worker is None:
            prompt.status = PromptStatus.STALE
            prompt.error = "worker disappeared before semantic review"
            prompt.completed_at = prompt.updated_at = datetime.now(UTC)
            await self.store.save_prompt(prompt)
            raise ValueError(prompt.error)
        extracted = PromptExtractor.extract(worker)
        if extracted is None or extracted.prompt_signature != prompt.prompt_signature:
            prompt.status = PromptStatus.STALE
            prompt.error = "prompt changed before semantic review"
            prompt.completed_at = prompt.updated_at = datetime.now(UTC)
            await self.store.save_prompt(prompt)
            raise ValueError(prompt.error)
        if worker.observation.content_fingerprint != prompt.observation_fingerprint:
            prompt.observation_fingerprint = worker.observation.content_fingerprint
        prompt.tier = tier
        prompt.status = PromptStatus.EVALUATING
        prompt.review_decisions = {}
        prompt.review_choices = {}
        prompt.review_rationales = {}
        prompt.reviewer_ids = []
        prompt.updated_at = datetime.now(UTC)
        await self.store.save_prompt(prompt)
        notification = await scheduler.enqueue_prompt_review(
            prompt.prompt_id, tier.value, prompt.worker_id,
            prompt.observation_fingerprint,
        )
        prompt.review_notification_id = notification.notification_id
        prompt.updated_at = datetime.now(UTC)
        await self.store.save_prompt(prompt)
        return notification

    async def review_completed(self, notification: CouncilNotification) -> None:
        if not notification.prompt_id:
            return
        prompt = await self.store.get_prompt(notification.prompt_id)
        if prompt is None or prompt.status in TERMINAL_STATUSES:
            return
        now = datetime.now(UTC)
        decisions = list(prompt.review_decisions.values())
        missing_reviews = set(notification.completion_signals) - set(
            prompt.review_decisions
        )
        incomplete_review = (
            notification.status != NotificationStatus.COMPLETED
            or not decisions
            or bool(missing_reviews)
        )
        unusable_fast_decision = (
            prompt.tier == ReviewTier.FAST
            and (
                PromptDecision.ESCALATE in decisions
                or (
                    decisions
                    and all(value == PromptDecision.ALLOW for value in decisions)
                    and len({value for value in prompt.review_choices.values() if value}) != 1
                )
            )
        )
        if (
            prompt.tier == ReviewTier.FAST
            and (incomplete_review or unusable_fast_decision)
            and self.review_scheduler is not None
        ):
            await self._emit(
                prompt,
                "Fast prompt review was inconclusive; promoting to full council",
                "warning",
            )
            await self.request_review(
                prompt.prompt_id, ReviewTier.COUNCIL, self.review_scheduler
            )
            return
        if (
            incomplete_review
        ):
            prompt.status = PromptStatus.ESCALATED
            prompt.tier = ReviewTier.HUMAN
            prompt.error = (
                notification.error
                or ("missing typed reviews from " + ", ".join(sorted(missing_reviews))
                    if missing_reviews else "semantic review produced no decision")
            )
        else:
            substantive = [d for d in decisions if d != PromptDecision.ESCALATE]
            if not substantive:
                prompt.status = PromptStatus.ESCALATED
                prompt.tier = ReviewTier.HUMAN
                prompt.error = "all reviewers escalated; no substantive decision"
            elif PromptDecision.DENY in substantive:
                prompt.decision = PromptDecision.DENY
                prompt.selected_choice = next(
                    (prompt.review_choices.get(key) for key, value in prompt.review_decisions.items()
                     if value == PromptDecision.DENY and prompt.review_choices.get(key)),
                    "deny",
                )
                prompt.status = PromptStatus.DECIDED
            elif all(value == PromptDecision.ALLOW for value in substantive):
                choices = {
                    prompt.review_choices[key]
                    for key, value in prompt.review_decisions.items()
                    if value == PromptDecision.ALLOW and prompt.review_choices.get(key)
                }
                if len(choices) == 1:
                    prompt.decision = PromptDecision.ALLOW
                    prompt.selected_choice = choices.pop()
                    prompt.status = PromptStatus.DECIDED
                else:
                    prompt.status = PromptStatus.ESCALATED
                    prompt.tier = ReviewTier.HUMAN
                    prompt.error = "reviewers did not agree on one executable choice"
            else:
                prompt.status = PromptStatus.ESCALATED
                prompt.tier = ReviewTier.HUMAN
                prompt.error = "semantic reviewers requested human escalation"
        if prompt.status == PromptStatus.DECIDED:
            prompt.decision_source = prompt.tier.value
            prompt.decided_at = now
            prompt.rationale = "; ".join(prompt.review_rationales.values())
        prompt.updated_at = now
        await self.store.save_prompt(prompt)
        await self._emit(
            prompt,
            f"Prompt semantic review completed: {prompt.status}",
            "warning" if prompt.status == PromptStatus.ESCALATED else "info",
        )
        if (
            prompt.status == PromptStatus.DECIDED
            and prompt.decision in {PromptDecision.ALLOW, PromptDecision.DENY}
            and self.action_arbiter is not None
        ):
            settings = await self.store.get_automation_settings()
            if (
                settings.automation_enabled
                and not settings.dry_run
                and not settings.paused
                and prompt.worker_id in settings.auto_yes_workers
            ):
                try:
                    await self.action_arbiter.execute(prompt.prompt_id)
                except RuntimeError as exc:
                    await self._emit(
                        prompt, f"Reviewed Auto yes action was not executed: {exc}",
                        "warning",
                    )

    async def _emit(
        self, prompt: PromptRequest, message: str, severity: str = "info"
    ) -> None:
        await self.inventory.emit(
            WallEvent(
                actor="prompt-arbiter",
                kind=EventKind.PROMPT,
                message=message,
                worker_id=prompt.worker_id,
                host=prompt.host,
                severity=severity,
                data={"prompt_id": prompt.prompt_id, "status": prompt.status},
            )
        )
