from datetime import timedelta

from agent_overlord.domain.workers import InputKind, WorkerState
from agent_overlord.services.classifier import WorkerClassifier


def test_classifies_agent_permission(config, codex_observation) -> None:
    worker = WorkerClassifier(config).classify(codex_observation)
    assert WorkerClassifier.is_agent(codex_observation)
    assert worker.harness == "codex"
    assert worker.model.lower() == "gpt-5"
    assert worker.state == WorkerState.AWAITING_INPUT
    assert worker.input_kind == InputKind.PERMISSION
    assert worker.project == "example"
    assert worker.confidence >= 0.8


def test_classifies_unchanged_worker_as_stalled(config, codex_observation) -> None:
    classifier = WorkerClassifier(config)
    codex_observation.content = ["Thinking about implementation"]
    first = classifier.classify(codex_observation)
    first.unchanged_since -= timedelta(seconds=61)
    second = classifier.classify(codex_observation.model_copy(deep=True), first)
    assert second.state == WorkerState.STALLED
    assert "unchanged" in second.evidence[0]


def test_harness_uses_ui_signatures_not_incidental_model_words(
    config, codex_observation
) -> None:
    codex_observation.current_command = "node"
    codex_observation.start_command = ""
    codex_observation.pane_title = "agent-overlord"
    codex_observation.content = [
        "We should mix Claude and Codex control agents",
        "Model: gpt-5.6",
        "To continue this session, run codex resume abc123",
    ]
    worker = WorkerClassifier(config).classify(codex_observation)
    assert worker.harness == "codex"
    assert worker.model == "gpt-5.6"


def test_idle_shell_does_not_become_stalled(config, codex_observation) -> None:
    classifier = WorkerClassifier(config)
    first = classifier.classify(codex_observation)
    first.unchanged_since -= timedelta(seconds=61)
    observation = codex_observation.model_copy(deep=True)
    observation.current_command = "bash"
    observation.content = ["jtanner@example:~/workspace/project$"]
    worker = classifier.classify(observation, first)
    assert worker.harness == "codex"
    assert worker.state == WorkerState.IDLE


def test_detects_claude_vertex_running_below_a_bash_pane(
    config, codex_observation
) -> None:
    observation = codex_observation.model_copy(deep=True)
    observation.current_command = "bash"
    observation.start_command = ""
    observation.pane_title = "Find Slack thread about API tier manifest"
    observation.content = ["Done. The plan has been saved.", "❯"]
    observation.descendant_commands = [
        "/bin/bash /home/jtanner/bin/claude.vertex",
        "/home/jtanner/.local/bin/claude",
        "python -m assistant_mcp",
    ]

    assert WorkerClassifier.is_agent(observation)
    assert WorkerClassifier(config).classify(observation).harness == "claude.vertex"


def test_completed_wrapped_agent_ignores_stale_permission_text(
    config, codex_observation
) -> None:
    classifier = WorkerClassifier(config)
    running = codex_observation.model_copy(deep=True)
    running.current_command = "bash"
    running.start_command = ""
    running.pane_title = "Analyze kserve architecture"
    running.descendant_commands = [
        "/bin/bash /home/jtanner/bin/claude.vertex",
        "/home/jtanner/.local/bin/claude",
    ]
    running.content = ["Working on the requested analysis"]
    previous = classifier.classify(running)

    completed = running.model_copy(deep=True)
    completed.descendant_commands = []
    completed.content = [
        '{"permission_required": true}',
        '{"terminal_reason": "completed"}',
        "jtanner@laptop:~/workspace/project$",
    ]
    worker = classifier.classify(completed, previous)

    assert worker.harness == "claude.vertex"
    assert worker.state == WorkerState.IDLE
    assert worker.awaiting_input is False
    assert worker.input_kind is None


def test_wrapped_agent_below_bash_still_detects_live_permission(
    config, codex_observation
) -> None:
    observation = codex_observation.model_copy(deep=True)
    observation.current_command = "bash"
    observation.start_command = ""
    observation.descendant_commands = ["/home/jtanner/bin/claude.vertex"]
    observation.content = ["Allow this test command? (y/n)"]

    worker = WorkerClassifier(config).classify(observation)

    assert worker.harness == "claude.vertex"
    assert worker.state == WorkerState.AWAITING_INPUT
    assert worker.input_kind == InputKind.PERMISSION


def test_narration_about_confirming_results_is_not_input(
    config, codex_observation
) -> None:
    observation = codex_observation.model_copy(deep=True)
    observation.content = [
        "I’m running the full suite one final time to confirm the complete result.",
        "Running tests now",
    ]

    worker = WorkerClassifier(config).classify(observation)

    assert worker.awaiting_input is False
    assert worker.state == WorkerState.ACTIVE


def test_actionable_confirmation_remains_input(config, codex_observation) -> None:
    observation = codex_observation.model_copy(deep=True)
    observation.content = ["Apply these changes?", "Please confirm?"]

    worker = WorkerClassifier(config).classify(observation)

    assert worker.awaiting_input is True
    assert worker.input_kind == InputKind.CONFIRMATION
