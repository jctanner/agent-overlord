from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_overlord.domain.prompts import (
    ApprovalPolicy,
    AutomationSettings,
    MatchKind,
    PolicyStatus,
    PromptDecision,
    PromptRisk,
    PromptStatus,
    ReviewTier,
)
from agent_overlord.domain.council import CouncilNotification, NotificationStatus
from agent_overlord.services.actions import ActionRejected, PromptActionArbiter
from agent_overlord.services.classifier import WorkerClassifier
from agent_overlord.services.inventory import InventoryService
from agent_overlord.services.prompts import PromptExtractor, PromptService
from agent_overlord.storage.sqlite import SQLiteStore


def permission_worker(config, observation):
    observation.content = [
        "Permission required to run command",
        "`uv run pytest tests/test_api.py -q`",
        "Allow command? (y/n)",
    ]
    return WorkerClassifier(config).classify(observation)


def test_extracts_structured_prompt_and_conservative_risk(config, codex_observation) -> None:
    worker = permission_worker(config, codex_observation)
    prompt = PromptExtractor.extract(worker)
    assert prompt is not None
    assert prompt.operation == "uv run pytest tests/test_api.py -q"
    assert prompt.normalized_argv == ["uv", "run", "pytest", "tests/test_api.py", "-q"]
    assert {item.choice for item in prompt.choices} == {"allow", "deny"}
    assert prompt.risk == PromptRisk.ROUTINE
    assert prompt.tier == ReviewTier.HUMAN

    codex_observation.content = ["Allow `sudo rm -rf /tmp/example`? (y/n)"]
    dangerous = PromptExtractor.extract(WorkerClassifier(config).classify(codex_observation))
    assert dangerous is not None
    assert dangerous.risk == PromptRisk.HIGH
    assert dangerous.tier == ReviewTier.COUNCIL


def test_worker_auto_yes_is_pane_scoped_and_refuses_high_or_unknown_risk(
    config, codex_observation
) -> None:
    prompt = PromptExtractor.extract(permission_worker(config, codex_observation))
    assert prompt is not None
    settings = AutomationSettings(auto_yes_workers=[prompt.worker_id])

    assert PromptService._worker_auto_yes(prompt, settings)
    assert not PromptService._worker_auto_yes(
        prompt.model_copy(update={"worker_id": "another-worker"}), settings
    )
    assert not PromptService._worker_auto_yes(
        prompt.model_copy(update={"risk": PromptRisk.HIGH}), settings
    )
    assert not PromptService._worker_auto_yes(
        prompt.model_copy(update={"risk": PromptRisk.UNKNOWN}), settings
    )


def test_claude_prompt_remains_live_above_task_footer(config, codex_observation) -> None:
    codex_observation.current_command = "claude"
    codex_observation.start_command = "claude"
    codex_observation.pane_title = "Action Required"
    codex_observation.content = [
        "────────────────────────────────────────",
        " Bash command",
        "",
        "   source .venv/bin/activate && python -m pytest tests/e2e/ -x -q",
        "   Run E2E tests",
        "",
        " 'source' evaluates arguments as shell code",
        "",
        " Do you want to proceed?",
        " ❯ 1. Yes",
        "   2. No",
        "",
        " Esc to cancel · Tab to amend · ctrl+e to explain",
        " 7 tasks (3 done, 3 in progress, 1 open)",
        " ◼ BUG-002: OpenAPI contract and contract tests",
        " ◻ Final audit: rerun all tests › blocked by #13",
        " ✔ BUG-006: Fix artifact path containment",
        " … +2 completed",
    ]
    worker = WorkerClassifier(config).classify(codex_observation)

    prompt = PromptExtractor.extract(worker)

    assert prompt is not None
    assert prompt.operation.startswith("source .venv/bin/activate")
    assert [(choice.choice, choice.response) for choice in prompt.choices] == [
        ("allow", "1"), ("deny", "2")
    ]


def test_reconstructs_wrapped_claude_command_before_description(
    config, codex_observation
) -> None:
    codex_observation.current_command = "claude"
    codex_observation.start_command = "claude"
    codex_observation.content = [
        " Bash command",
        "   source .venv/bin/activate && echo \"=== unit ===\" && python -m pytest -m unit -q && echo \"===",
        "   integration ===\" && python -m pytest -m integration -q && echo \"=== e2e ===\" && python -m pytest",
        "   -m e2e -q 2>&1 | tail -3",
        "   Run all marker selections",
        "",
        " 'source' evaluates arguments as shell code",
        " Do you want to proceed?",
        " ❯ 1. Yes",
        "   2. No",
        " Esc to cancel · Tab to amend",
    ]
    prompt = PromptExtractor.extract(WorkerClassifier(config).classify(codex_observation))

    assert prompt is not None
    assert prompt.operation.endswith("-m e2e -q 2>&1 | tail -3")
    assert prompt.normalized_argv
    assert prompt.risk == PromptRisk.ROUTINE


def test_genuine_output_after_old_prompt_is_not_live(config, codex_observation) -> None:
    worker = permission_worker(config, codex_observation)
    worker.observation.content.extend([
        "● Ran uv run pytest",
        "12 tests passed",
    ])

    assert PromptExtractor.extract(worker) is None


def test_codex_selected_chevron_and_read_only_sed_are_routine(
    config, codex_observation
) -> None:
    codex_observation.content = [
        "Would you like to run the following command?",
        "$ sed -n '1,260p' src/frontend/ClaimConsolidation.tsx",
        "› 1. Yes, proceed (y)",
        "  2. Yes, and don't ask again for commands that start with `sed -n` (p)",
        "  3. No, and tell Codex what to do differently (esc)",
        "Press enter to confirm or esc to cancel",
    ]
    prompt = PromptExtractor.extract(WorkerClassifier(config).classify(codex_observation))

    assert prompt is not None
    assert prompt.risk == PromptRisk.ROUTINE
    assert {choice.choice for choice in prompt.choices} == {
        "allow", "allow_always", "deny"
    }


async def test_visible_prompt_does_not_expire_and_unknown_auto_yes_gets_fast_review(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "live-review.db")
    await store.initialize()
    inventory = InventoryService(config, store)
    service = PromptService(store, inventory)

    old_worker = permission_worker(config, codex_observation)
    inventory.workers[old_worker.worker_id] = old_worker
    old_prompt = PromptExtractor.extract(old_worker)
    assert old_prompt is not None
    old_prompt.created_at = datetime.now(UTC) - timedelta(hours=1)
    await store.save_prompt(old_prompt)
    await store.save_automation_settings(AutomationSettings(prompt_expiration_secs=1))

    await service.observe_workers([old_worker])
    assert (await store.get_prompt(old_prompt.prompt_id)).status == PromptStatus.DETECTED

    unknown_observation = codex_observation.model_copy(deep=True)
    unknown_observation.content = [
        "Would you like to run the following command?",
        "$ ./project-specific-tool --inspect",
        "› 1. Yes, proceed (y)",
        "  2. No (esc)",
        "Press enter to confirm or esc to cancel",
    ]
    unknown_worker = WorkerClassifier(config).classify(unknown_observation)
    inventory.workers[unknown_worker.worker_id] = unknown_worker
    settings = AutomationSettings(auto_yes_workers=[unknown_worker.worker_id])
    await store.save_automation_settings(settings)

    class Scheduler:
        calls: list[tuple[str, str, str, str]] = []

        async def enqueue_prompt_review(self, *args):
            self.calls.append(args)
            return SimpleNamespace(notification_id="fast-review")

    scheduler = Scheduler()
    service.review_scheduler = scheduler
    await service.observe_workers([unknown_worker])

    reviewed = (await store.list_prompts(status=PromptStatus.EVALUATING))[0]
    assert reviewed.tier == ReviewTier.FAST
    assert reviewed.review_notification_id == "fast-review"
    assert scheduler.calls


@pytest.mark.parametrize(
    ("operation", "expected_risk", "expected_tier"),
    [
        ("uv run pytest tests/test_api.py -q", PromptRisk.ROUTINE, ReviewTier.HUMAN),
        ("ruff check src tests", PromptRisk.ROUTINE, ReviewTier.HUMAN),
        ("git status --short", PromptRisk.ROUTINE, ReviewTier.HUMAN),
        ("curl -fsS https://example.test/data", PromptRisk.ELEVATED, ReviewTier.FAST),
        ("pip install example", PromptRisk.ELEVATED, ReviewTier.FAST),
        ("npm install example", PromptRisk.ELEVATED, ReviewTier.FAST),
        ("rm -rf build", PromptRisk.HIGH, ReviewTier.COUNCIL),
        ("git push origin main", PromptRisk.HIGH, ReviewTier.COUNCIL),
        ("kubectl apply -f deploy.yaml", PromptRisk.HIGH, ReviewTier.COUNCIL),
        ("./project-specific-tool --change", PromptRisk.UNKNOWN, ReviewTier.HUMAN),
    ],
)
def test_dry_run_risk_corpus_never_routes_nonroutine_work_to_policy(
    config, codex_observation, operation, expected_risk, expected_tier
) -> None:
    codex_observation.content = [f"`{operation}`", "Allow command? (y/n)"]
    prompt = PromptExtractor.extract(WorkerClassifier(config).classify(codex_observation))
    assert prompt is not None
    assert prompt.risk == expected_risk
    assert prompt.tier == expected_tier
    if expected_risk != PromptRisk.ROUTINE:
        assert prompt.tier != ReviewTier.POLICY


async def test_prompt_lifecycle_coalesces_and_stales_changed_observations(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "prompts.db")
    await store.initialize()
    inventory = InventoryService(config, store)
    service = PromptService(store, inventory)
    worker = permission_worker(config, codex_observation)

    await service.observe_workers([worker])
    await service.observe_workers([worker])
    prompts = await store.list_prompts()
    assert len(prompts) == 1
    assert prompts[0].status == PromptStatus.DETECTED

    changed = worker.model_copy(deep=True)
    changed.observation.content.append("command started")
    changed.awaiting_input = False
    await service.observe_workers([changed])
    assert (await store.get_prompt(prompts[0].prompt_id)).status == PromptStatus.STALE


async def test_policy_uses_structured_exact_or_argv_prefix_matching(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "policy.db")
    await store.initialize()
    inventory = InventoryService(config, store)
    service = PromptService(store, inventory)
    prompt = PromptExtractor.extract(permission_worker(config, codex_observation))
    assert prompt is not None
    exact = ApprovalPolicy(
        name="project tests", decision=PromptDecision.ALLOW,
        command_argv=prompt.normalized_argv, project=prompt.project,
    )
    await store.save_policy(exact)
    assert (await service.match_policy(prompt)).policy_id == exact.policy_id

    exact.status = PolicyStatus.REVOKED
    await store.save_policy(exact)
    prefix = ApprovalPolicy(
        name="pytest in project", decision=PromptDecision.ALLOW,
        match_kind=MatchKind.ARGV_PREFIX, command_argv=["uv", "run", "pytest"],
        project=prompt.project,
    )
    await store.save_policy(prefix)
    assert (await service.match_policy(prompt)).policy_id == prefix.policy_id

    prompt.normalized_argv = ["uv", "run", "pytest-malicious"]
    assert await service.match_policy(prompt) is None
    prompt.normalized_argv = ["uv", "run", "pytest", "&&", "rm", "-rf", "/tmp/x"]
    prompt.risk = PromptRisk.ROUTINE
    assert await service.match_policy(prompt) is None


class FakeTransport:
    def __init__(self, before: list[str]) -> None:
        self.content = before
        self.calls: list[tuple[str, ...]] = []

    async def run_tmux(self, *args: str, timeout: float = 15.0) -> str:
        self.calls.append(args)
        if args[0] == "capture-pane":
            return "\n".join(self.content) + "\n"
        if args[0] == "send-keys" and args[-1] == "Enter":
            self.content = [*self.content, "Running tests now"]
        return ""


async def test_action_arbiter_recaptures_sends_bounded_choice_and_verifies(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "actions.db")
    await store.initialize()
    await store.save_automation_settings(AutomationSettings(dry_run=False))
    inventory = InventoryService(config, store)
    worker = permission_worker(config, codex_observation)
    inventory.workers[worker.worker_id] = worker
    prompt = PromptExtractor.extract(worker)
    assert prompt is not None
    await store.save_prompt(prompt)
    transport = FakeTransport(worker.observation.content)
    inventory.discovery.transports[worker.observation.host] = transport
    arbiter = PromptActionArbiter(store, inventory)

    decided = await arbiter.decide(
        prompt.prompt_id, PromptDecision.ALLOW, "allow", source="human",
        rationale="one-time test approval",
        expected_fingerprint=prompt.observation_fingerprint,
        expected_worker_id=prompt.worker_id,
        expected_pane_id=prompt.pane_id,
    )
    result = await arbiter.execute(decided.prompt_id)

    assert result.status == PromptStatus.SUCCEEDED
    send_calls = [call for call in transport.calls if call[0] == "send-keys"]
    assert send_calls == [
        ("send-keys", "-t", prompt.pane_id, "-l", "y"),
        ("send-keys", "-t", prompt.pane_id, "Enter"),
    ]
    assert result.pre_action_fingerprint == prompt.observation_fingerprint
    assert result.post_action_fingerprint != result.pre_action_fingerprint


async def test_action_arbiter_rejects_stale_prompt_without_sending_input(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "stale-action.db")
    await store.initialize()
    await store.save_automation_settings(AutomationSettings(dry_run=False))
    inventory = InventoryService(config, store)
    worker = permission_worker(config, codex_observation)
    inventory.workers[worker.worker_id] = worker
    prompt = PromptExtractor.extract(worker)
    assert prompt is not None
    prompt.status = PromptStatus.DECIDED
    prompt.decision = PromptDecision.ALLOW
    prompt.selected_choice = "allow"
    await store.save_prompt(prompt)
    transport = FakeTransport([*worker.observation.content, "prompt changed"])
    inventory.discovery.transports[worker.observation.host] = transport

    result = await PromptActionArbiter(store, inventory).execute(prompt.prompt_id)

    assert result.status == PromptStatus.STALE
    assert not any(call[0] == "send-keys" for call in transport.calls)


async def test_repeated_verified_human_decisions_create_candidate_not_authority(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "candidate.db")
    await store.initialize()
    inventory = InventoryService(config, store)
    base = PromptExtractor.extract(permission_worker(config, codex_observation))
    assert base is not None
    arbiter = PromptActionArbiter(store, inventory)
    for index in range(3):
        item = base.model_copy(
            update={
                "prompt_id": f"candidate-{index}",
                "observation_fingerprint": str(index) * 64,
                "status": PromptStatus.SUCCEEDED,
                "decision": PromptDecision.ALLOW,
                "selected_choice": "allow",
                "decision_source": "human",
            },
            deep=True,
        )
        await store.save_prompt(item)
    await arbiter._maybe_propose_policy(item)

    policies = await store.list_policies(include_inactive=True)
    assert len(policies) == 1
    assert policies[0].status == PolicyStatus.CANDIDATE
    assert await store.list_policies() == []


async def test_full_review_requires_typed_decision_from_every_completed_controller(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "review.db")
    await store.initialize()
    inventory = InventoryService(config, store)
    service = PromptService(store, inventory)
    prompt = PromptExtractor.extract(permission_worker(config, codex_observation))
    assert prompt is not None
    prompt.status = PromptStatus.EVALUATING
    prompt.tier = ReviewTier.COUNCIL
    prompt.review_decisions = {"operator": PromptDecision.ALLOW}
    prompt.review_choices = {"operator": "allow"}
    await store.save_prompt(prompt)
    notification = CouncilNotification(
        reason="prompt_review_council", prompt_id=prompt.prompt_id,
        status=NotificationStatus.COMPLETED,
        completion_signals={"operator": "done", "auditor": "done", "strategist": "done"},
    )

    await service.review_completed(notification)

    reviewed = await store.get_prompt(prompt.prompt_id)
    assert reviewed.status == PromptStatus.ESCALATED
    assert "auditor" in reviewed.error and "strategist" in reviewed.error


async def test_fast_review_allow_executes_for_auto_yes_worker(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "review-auto-action.db")
    await store.initialize()
    inventory = InventoryService(config, store)
    worker = permission_worker(config, codex_observation)
    inventory.workers[worker.worker_id] = worker
    prompt = PromptExtractor.extract(worker)
    assert prompt is not None
    prompt.risk = PromptRisk.UNKNOWN
    prompt.status = PromptStatus.EVALUATING
    prompt.tier = ReviewTier.FAST
    prompt.review_decisions = {"auditor": PromptDecision.ALLOW}
    prompt.review_choices = {"auditor": "allow"}
    prompt.review_rationales = {"auditor": "captured operation is safe"}
    await store.save_prompt(prompt)
    await store.save_automation_settings(AutomationSettings(
        automation_enabled=True, dry_run=False,
        auto_yes_workers=[worker.worker_id],
    ))
    transport = FakeTransport(worker.observation.content)
    inventory.discovery.transports[worker.observation.host] = transport
    service = PromptService(store, inventory)
    service.action_arbiter = PromptActionArbiter(store, inventory)
    notification = CouncilNotification(
        reason="prompt_review_fast", prompt_id=prompt.prompt_id,
        status=NotificationStatus.COMPLETED,
        completion_signals={"auditor": "done"},
    )

    await service.review_completed(notification)

    reviewed = await store.get_prompt(prompt.prompt_id)
    assert reviewed.status == PromptStatus.SUCCEEDED
    assert reviewed.decision_source == ReviewTier.FAST
    assert any(call[0] == "send-keys" for call in transport.calls)


async def test_fast_review_deny_sends_bounded_denial_for_auto_yes_worker(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "review-auto-deny.db")
    await store.initialize()
    inventory = InventoryService(config, store)
    worker = permission_worker(config, codex_observation)
    inventory.workers[worker.worker_id] = worker
    prompt = PromptExtractor.extract(worker)
    assert prompt is not None
    prompt.status = PromptStatus.EVALUATING
    prompt.tier = ReviewTier.FAST
    prompt.review_decisions = {"auditor": PromptDecision.DENY}
    prompt.review_choices = {"auditor": "deny"}
    prompt.review_rationales = {"auditor": "command could overwrite a file"}
    await store.save_prompt(prompt)
    await store.save_automation_settings(AutomationSettings(
        automation_enabled=True, dry_run=False,
        auto_yes_workers=[worker.worker_id],
    ))
    transport = FakeTransport(worker.observation.content)
    inventory.discovery.transports[worker.observation.host] = transport
    service = PromptService(store, inventory)
    service.action_arbiter = PromptActionArbiter(store, inventory)
    notification = CouncilNotification(
        reason="prompt_review_fast", prompt_id=prompt.prompt_id,
        status=NotificationStatus.COMPLETED,
        completion_signals={"auditor": "done"},
    )

    await service.review_completed(notification)

    reviewed = await store.get_prompt(prompt.prompt_id)
    assert reviewed.status == PromptStatus.SUCCEEDED
    assert reviewed.decision == PromptDecision.DENY
    assert reviewed.selected_choice == "deny"
    assert [call for call in transport.calls if call[0] == "send-keys"] == [
        ("send-keys", "-t", prompt.pane_id, "-l", "n"),
        ("send-keys", "-t", prompt.pane_id, "Enter"),
    ]


async def test_fast_review_timeout_promotes_to_full_council(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "review-promotion.db")
    await store.initialize()
    inventory = InventoryService(config, store)
    worker = permission_worker(config, codex_observation)
    inventory.workers[worker.worker_id] = worker
    prompt = PromptExtractor.extract(worker)
    assert prompt is not None
    prompt.status = PromptStatus.EVALUATING
    prompt.tier = ReviewTier.FAST
    await store.save_prompt(prompt)

    class Scheduler:
        calls: list[tuple[str, str, str, str]] = []

        async def enqueue_prompt_review(self, *args):
            self.calls.append(args)
            return SimpleNamespace(notification_id="full-council-review")

    scheduler = Scheduler()
    service = PromptService(store, inventory)
    service.review_scheduler = scheduler
    timed_out = CouncilNotification(
        reason="prompt_review_fast", prompt_id=prompt.prompt_id,
        status=NotificationStatus.FAILED,
        completion_signals={"auditor": "auditor timed out after 60s"},
        error="auditor timed out after 60s",
    )

    await service.review_completed(timed_out)

    promoted = await store.get_prompt(prompt.prompt_id)
    assert promoted.status == PromptStatus.EVALUATING
    assert promoted.tier == ReviewTier.COUNCIL
    assert promoted.review_notification_id == "full-council-review"
    assert scheduler.calls[0][1] == ReviewTier.COUNCIL


async def test_exact_verified_council_precedent_executes_without_new_review(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "review-precedent.db")
    await store.initialize()
    inventory = InventoryService(config, store)
    codex_observation.content = [
        "Would you like to run the following command?",
        "$ ./project-specific-tool --inspect",
        "› 1. Yes, proceed (y)",
        "  2. No (esc)",
        "Press enter to confirm or esc to cancel",
    ]
    prior_worker = WorkerClassifier(config).classify(codex_observation)
    prior = PromptExtractor.extract(prior_worker)
    assert prior is not None and prior.risk == PromptRisk.UNKNOWN
    prior.status = PromptStatus.SUCCEEDED
    prior.decision = PromptDecision.ALLOW
    prior.selected_choice = "allow"
    prior.decision_source = ReviewTier.COUNCIL.value
    prior.pre_action_fingerprint = prior.observation_fingerprint
    prior.post_action_fingerprint = "f" * 64
    prior.completed_at = prior.updated_at = datetime.now(UTC)
    await store.save_prompt(prior)

    repeated_observation = codex_observation.model_copy(deep=True)
    repeated_observation.content.insert(0, "A later exact request")
    worker = WorkerClassifier(config).classify(repeated_observation)
    inventory.workers[worker.worker_id] = worker
    await store.save_automation_settings(AutomationSettings(
        automation_enabled=True, dry_run=False,
        auto_yes_workers=[worker.worker_id],
    ))
    transport = FakeTransport(worker.observation.content)
    inventory.discovery.transports[worker.observation.host] = transport
    service = PromptService(store, inventory)
    service.action_arbiter = PromptActionArbiter(store, inventory)

    await service.observe_workers([worker])

    repeated = next(
        item for item in await store.list_prompts()
        if item.observation_fingerprint == worker.observation.content_fingerprint
    )
    assert repeated.status == PromptStatus.SUCCEEDED
    assert repeated.decision_source == "review_precedent"
    assert prior.prompt_id in repeated.rationale
    assert any(call[0] == "send-keys" for call in transport.calls)

    high_risk = repeated.model_copy(update={"risk": PromptRisk.HIGH})
    settings = await store.get_automation_settings()
    assert await service.match_review_precedent(high_risk, settings) is None


async def test_rate_limit_persists_retry_time_and_auto_yes_uses_separate_limit(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "auto-rate-limit.db")
    await store.initialize()
    inventory = InventoryService(config, store)
    worker = permission_worker(config, codex_observation)
    inventory.workers[worker.worker_id] = worker
    prompt = PromptExtractor.extract(worker)
    assert prompt is not None
    for index in range(2):
        prior = prompt.model_copy(update={
            "prompt_id": f"prior-rate-{index}",
            "observation_fingerprint": str(index) * 64,
            "status": PromptStatus.SUCCEEDED,
            "completed_at": datetime.now(UTC),
        })
        await store.save_prompt(prior)
    arbiter = PromptActionArbiter(store, inventory)
    prompt.status = PromptStatus.DECIDED
    prompt.decision = PromptDecision.ALLOW
    prompt.selected_choice = "allow"
    prompt.decision_source = "worker_auto_yes"
    await store.save_prompt(prompt)

    with pytest.raises(ActionRejected, match="rate limited until"):
        await arbiter._check_rate_limit(prompt, 2)
    limited = await store.get_prompt(prompt.prompt_id)
    assert limited.status == PromptStatus.DECIDED
    assert limited.error.startswith("rate limited until ")

    await store.save_automation_settings(AutomationSettings(
        automation_enabled=True, dry_run=False,
        max_actions_per_pane_per_hour=2,
        auto_yes_max_actions_per_worker_per_hour=3,
        auto_yes_workers=[worker.worker_id],
    ))
    transport = FakeTransport(worker.observation.content)
    inventory.discovery.transports[worker.observation.host] = transport

    result = await arbiter.execute(prompt.prompt_id)

    assert result.status == PromptStatus.SUCCEEDED
    assert result.error is None


async def test_active_routine_policy_executes_without_council_when_enabled(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "tier-zero.db")
    await store.initialize()
    await store.save_automation_settings(
        AutomationSettings(automation_enabled=True, dry_run=False)
    )
    inventory = InventoryService(config, store)
    worker = permission_worker(config, codex_observation)
    inventory.workers[worker.worker_id] = worker
    prompt = PromptExtractor.extract(worker)
    assert prompt is not None
    await store.save_policy(ApprovalPolicy(
        name="exact test", decision=PromptDecision.ALLOW,
        command_argv=prompt.normalized_argv, allowed_choices=["allow"],
        harness=prompt.harness, host=prompt.host, project=prompt.project,
    ))
    transport = FakeTransport(worker.observation.content)
    inventory.discovery.transports[worker.observation.host] = transport
    service = PromptService(store, inventory)
    service.action_arbiter = PromptActionArbiter(store, inventory)

    await service.observe_workers([worker])

    records = await store.list_prompts()
    assert len(records) == 1
    assert records[0].status == PromptStatus.SUCCEEDED
    assert records[0].decision_source == "policy"
    assert await store.list_notifications() == []


async def test_pause_and_restart_recovery_never_repeat_uncertain_action(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "recovery-action.db")
    await store.initialize()
    await store.save_automation_settings(
        AutomationSettings(dry_run=False, paused=True)
    )
    inventory = InventoryService(config, store)
    worker = permission_worker(config, codex_observation)
    inventory.workers[worker.worker_id] = worker
    prompt = PromptExtractor.extract(worker)
    assert prompt is not None
    prompt.status = PromptStatus.DECIDED
    prompt.decision = PromptDecision.ALLOW
    prompt.selected_choice = "allow"
    await store.save_prompt(prompt)
    transport = FakeTransport(worker.observation.content)
    inventory.discovery.transports[worker.observation.host] = transport
    arbiter = PromptActionArbiter(store, inventory)

    with pytest.raises(ActionRejected, match="paused"):
        await arbiter.execute(prompt.prompt_id)
    assert not any(call[0] == "send-keys" for call in transport.calls)

    prompt.status = PromptStatus.EXECUTING
    await store.save_prompt(prompt)
    await PromptService(store, inventory).initialize()
    recovered = await store.get_prompt(prompt.prompt_id)
    assert recovered.status == PromptStatus.FAILED
    assert "not repeated" in recovered.error
