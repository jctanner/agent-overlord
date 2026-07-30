from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import PurePosixPath

from agent_overlord.config import AppConfig
from agent_overlord.domain.workers import InputKind, PaneObservation, Worker, WorkerState


SHELLS = {"bash", "zsh", "fish", "sh", "dash", "tcsh", "csh"}
AGENT_MARKERS = {
    "claude.vertex": ("claude.vertex", "vertex"),
    "claude": ("claude", "anthropic"),
    "codex": ("codex", "openai codex"),
    "goose": ("goose",),
    "hermes": ("hermes",),
}
FAILURE_PATTERNS = (
    re.compile(r"\b(error|failed|fatal|panic|traceback)\b", re.IGNORECASE),
    re.compile(r"tests? result:.*failed", re.IGNORECASE),
)
COMPLETE_PATTERNS = (
    re.compile(r"\b(task|work) (is )?complete\b", re.IGNORECASE),
    re.compile(r"\bfinished successfully\b", re.IGNORECASE),
)
INPUT_PATTERNS: tuple[tuple[InputKind, re.Pattern[str]], ...] = (
    (InputKind.CREDENTIAL, re.compile(r"password|passphrase|authentication", re.I)),
    (InputKind.PERMISSION, re.compile(r"permission|allow|approve|yes/no|\(y/n\)", re.I)),
    (InputKind.SELECTION, re.compile(r"select|choose|pick one|enter choice", re.I)),
    (
        InputKind.CONFIRMATION,
        re.compile(r"press enter(?:\s+to\s+confirm)?|\bconfirm\??\s*$", re.I),
    ),
    (InputKind.CLARIFICATION, re.compile(r"\?\s*$|need.*clarif", re.I)),
)
MODEL_PATTERN = re.compile(
    r"\b(claude[- ](?:opus|sonnet|haiku)[-\w.]*|gpt[- ][\w.]+|o[134](?:[-\w.]*)?)\b",
    re.IGNORECASE,
)
CODEX_MODEL_PATTERN = re.compile(r"\b(gpt[- ][\w.]+|o[134](?:[-\w.]*)?)\b", re.I)
CLAUDE_MODEL_PATTERN = re.compile(
    r"\b(claude[- ](?:opus|sonnet|haiku)[-\w.]*)\b", re.I
)
CONTEXT_PATTERN = re.compile(
    r"(?:context|tokens?)\s*[:=]?\s*(\d+(?:\.\d+)?%|\d+[kK]?\s*/\s*\d+[kK]?)",
    re.IGNORECASE,
)
PURPOSE_PATTERN = re.compile(
    r"(?:^|[•>*-]\s*)(?P<purpose>(?:working on|implementing|investigating|fixing|"
    r"reviewing|planning|debugging|researching)\b.{5,140})",
    re.IGNORECASE,
)


class WorkerClassifier:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def classify(self, observation: PaneObservation, previous: Worker | None = None) -> Worker:
        now = datetime.now(UTC)
        recent = observation.content[-20:]
        text = "\n".join(recent)
        metadata_and_output = " ".join(
            (
                observation.current_command,
                observation.start_command,
                observation.pane_title,
                *observation.descendant_commands,
                text,
            )
        )
        harness = self._harness(observation, text, previous)
        model_pattern = (
            CODEX_MODEL_PATTERN
            if harness == "codex"
            else CLAUDE_MODEL_PATTERN
            if harness.startswith("claude")
            else MODEL_PATTERN
        )
        model = self._match(model_pattern, metadata_and_output) or "unknown"
        context = self._match(CONTEXT_PATTERN, metadata_and_output) or "unknown"
        # A foreground shell with no descendants is conclusive current evidence
        # that a wrapped agent has exited. Do not let historical prompt language
        # in its retained transcript override that live process state.
        idle_shell = (
            observation.current_command in SHELLS
            and not observation.descendant_commands
        )
        input_kind = None if idle_shell else self._input_kind(recent)
        awaiting = not idle_shell and (
            input_kind is not None
            or any(
                pattern.lower() in text.lower()
                for pattern in self.config.attention_patterns
            )
        )

        state, state_evidence = self._state(
            observation, text, awaiting, previous, now
        )
        purpose, project, purpose_evidence = self._purpose(observation, harness)
        evidence = [state_evidence]
        if harness != "unknown":
            evidence.append(f"Harness marker matched {harness}")
        if observation.current_path:
            evidence.append(f"Working directory: {observation.current_path}")
        if purpose_evidence:
            evidence.append(purpose_evidence)

        unchanged_since = now
        first_seen = now
        if previous:
            first_seen = previous.first_seen_at
            if (
                previous.observation.content_fingerprint
                == observation.content_fingerprint
            ):
                unchanged_since = previous.unchanged_since

        return Worker(
            worker_id=observation.worker_id,
            observation=observation,
            harness=harness,
            model=model,
            context=context,
            purpose=purpose,
            project=project,
            state=state,
            awaiting_input=awaiting,
            input_kind=input_kind or (InputKind.UNKNOWN if awaiting else None),
            confidence=0.9 if harness != "unknown" else 0.55,
            evidence=evidence,
            first_seen_at=first_seen,
            last_seen_at=now,
            unchanged_since=unchanged_since,
        )

    @staticmethod
    def is_agent(observation: PaneObservation) -> bool:
        return WorkerClassifier._harness(
            observation, "\n".join(observation.content[-20:]), None
        ) != "unknown"

    @staticmethod
    def _harness(
        observation: PaneObservation, output: str, previous: Worker | None
    ) -> str:
        metadata = " ".join(
            (
                observation.current_command,
                observation.start_command,
                observation.pane_title,
                *observation.descendant_commands,
            )
        ).lower()
        output = output.lower()
        scores = {name: 0 for name in AGENT_MARKERS}

        if "claude.vertex" in metadata or ("claude" in metadata and "vertex" in metadata):
            scores["claude.vertex"] += 20
        for harness in ("codex", "claude", "goose", "hermes"):
            if re.search(rf"\b{re.escape(harness)}\b", metadata):
                scores[harness] += 15

        # Harness-specific terminal signatures carry more weight than incidental
        # mentions of another model in an agent's transcript.
        scores["codex"] += 8 * output.count("codex resume")
        scores["codex"] += 5 * output.count("to continue this session")
        scores["codex"] += 3 * output.count("ctrl + t to view transcript")
        scores["claude"] += 8 * output.count('"modelusage"')
        scores["claude"] += 5 * output.count('"total_cost_usd"')
        scores["claude"] += 4 * output.count("claude code")
        scores["goose"] += 6 * output.count("goose session")
        scores["hermes"] += 6 * output.count("hermes")

        best_score = max(scores.values())
        if best_score == 0:
            return previous.harness if previous else "unknown"
        tied = [name for name, score in scores.items() if score == best_score]
        if previous and previous.harness in tied:
            return previous.harness
        return tied[0]

    @staticmethod
    def _match(pattern: re.Pattern[str], text: str) -> str | None:
        match = pattern.search(text)
        return match.group(1) if match else None

    @staticmethod
    def _input_kind(lines: list[str]) -> InputKind | None:
        for line in reversed(lines[-15:]):
            for kind, pattern in INPUT_PATTERNS:
                if pattern.search(line):
                    return kind
        return None

    def _state(
        self,
        observation: PaneObservation,
        text: str,
        awaiting: bool,
        previous: Worker | None,
        now: datetime,
    ) -> tuple[WorkerState, str]:
        if observation.pane_dead:
            return WorkerState.FAILED, "tmux reports that the pane process is dead"
        if awaiting:
            return WorkerState.AWAITING_INPUT, "a recent input prompt was detected"
        if observation.current_command in SHELLS:
            return WorkerState.IDLE, f"foreground command is {observation.current_command}"
        if any(pattern.search(text) for pattern in FAILURE_PATTERNS):
            return WorkerState.FAILED, "failure language appears in recent output"
        if any(pattern.search(text) for pattern in COMPLETE_PATTERNS):
            return WorkerState.COMPLETE, "completion language appears in recent output"
        if previous and previous.observation.content_fingerprint == observation.content_fingerprint:
            unchanged = (now - previous.unchanged_since).total_seconds()
            if unchanged >= self.config.stalled_after_secs:
                return WorkerState.STALLED, f"output unchanged for {int(unchanged)} seconds"
        if observation.current_command:
            return WorkerState.ACTIVE, f"foreground command is {observation.current_command}"
        return WorkerState.UNKNOWN, "no reliable state signal was found"

    @staticmethod
    def _purpose(
        observation: PaneObservation, harness: str
    ) -> tuple[str, str | None, str | None]:
        project = None
        if observation.current_path:
            project = PurePosixPath(observation.current_path).name or None
        for line in reversed(observation.content[-30:]):
            match = PURPOSE_PATTERN.search(line.strip())
            if match:
                purpose = match.group("purpose").strip().rstrip(".")
                return purpose[0].upper() + purpose[1:], project, f"Recent output: {line.strip()}"
        label = " / ".join(
            item for item in (project, observation.session_name, observation.window_name) if item
        )
        if harness != "unknown":
            return (
                f"{harness} working in {label}" if label else f"{harness} agent",
                project,
                None,
            )
        command = observation.current_command or "process"
        return (f"{command} in {label}" if label else command, project, None)
