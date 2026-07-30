from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agent_overlord.config import ControllerConfig


@dataclass(slots=True)
class ControllerTurnOutput:
    response_text: str = ""
    thinking_text: str = ""
    session_id: str | None = None
    usage: dict | None = None


class ControllerBackend(Protocol):
    harness: str

    def generate_config(
        self, config: ControllerConfig, prompt: str, mcp_url: str, token: str, directory: Path
    ) -> dict[str, Path]: ...

    def mounts(self, files: dict[str, Path]) -> list[str]: ...

    def build_turn_command(
        self, container: str, config: ControllerConfig, prompt: str,
        session_id: str | None, first_turn: bool,
    ) -> list[str]: ...

    def parse_output(self, stdout: str) -> ControllerTurnOutput: ...


class ClaudeControllerBackend:
    harness = "claude.vertex"

    def generate_config(
        self, config: ControllerConfig, prompt: str, mcp_url: str, token: str, directory: Path
    ) -> dict[str, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        prompt_path = directory / f"{config.controller_id}-system.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        mcp_path = directory / f"{config.controller_id}-mcp.json"
        mcp_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "overlord": {
                            "type": "http",
                            "url": f"{mcp_url}/mcp/{config.controller_id}/mcp",
                            "headers": {"Authorization": f"Bearer {token}"},
                        }
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        prompt_path.chmod(0o600)
        mcp_path.chmod(0o600)
        return {"prompt": prompt_path, "mcp": mcp_path}

    def mounts(self, files: dict[str, Path]) -> list[str]:
        return [
            "-v", f"{files['prompt'].resolve()}:/home/agent/system-prompt.md:ro,Z",
            "-v", f"{files['mcp'].resolve()}:/home/agent/mcp.json:ro,Z",
        ]

    def build_turn_command(
        self, container: str, config: ControllerConfig, prompt: str,
        session_id: str | None, first_turn: bool,
    ) -> list[str]:
        command = ["podman", "exec", container, "claude"]
        if not first_turn and session_id:
            command += ["--resume", session_id]
        command += ["-p", prompt]
        if first_turn:
            command += ["--system-prompt-file", "/home/agent/system-prompt.md"]
            if session_id:
                command += ["--session-id", session_id]
        command += [
            "--mcp-config", "/home/agent/mcp.json",
            "--allowedTools", "mcp__overlord__*",
            "--output-format", "stream-json",
            "--verbose",
            "--model", config.model,
            "--max-turns", str(config.max_turns),
            "--permission-mode", "dontAsk",
        ]
        return command

    def parse_output(self, stdout: str) -> ControllerTurnOutput:
        result = ControllerTurnOutput(usage={})
        thinking: list[str] = []
        for line in stdout.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if value.get("type") == "result":
                result.response_text = value.get("result", "")
                result.session_id = value.get("session_id") or result.session_id
                result.usage = value.get("usage", {})
            elif value.get("type") == "assistant":
                for block in value.get("message", {}).get("content", []):
                    if block.get("type") == "thinking" and block.get("thinking"):
                        thinking.append(block["thinking"])
        result.thinking_text = "\n\n".join(thinking)
        return result


class CodexControllerBackend:
    harness = "codex"
    enabled_tools = [
        "list_workers", "get_worker", "get_worker_capture", "get_worker_history",
        "get_host_health", "get_chat_context", "get_project_context", "search_wall",
        "list_session_directory", "find_session_files", "read_session_file",
        "search_memories", "propose_memory", "get_interpretations",
        "record_interpretation", "post_wall_message", "submit_proposal",
        "critique_proposal", "vote_on_proposal", "answer_human_message", "signal_done",
        "get_prompt", "review_prompt",
    ]

    def generate_config(
        self, config: ControllerConfig, prompt: str, mcp_url: str, token: str, directory: Path
    ) -> dict[str, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        config_path = directory / f"{config.controller_id}-codex.toml"
        safe_prompt = prompt.replace('"""', "\\\"\\\"\\\"")
        config_path.write_text(
            f'instructions = """{safe_prompt}"""\n\n'
            "[mcp_servers.overlord]\n"
            f'url = "{mcp_url}/mcp/{config.controller_id}/mcp"\n'
            f'http_headers = {{ Authorization = "Bearer {token}" }}\n'
            "required = true\n"
            "default_tools_approval_mode = \"approve\"\n"
            f"enabled_tools = {json.dumps(self.enabled_tools)}\n",
            encoding="utf-8",
        )
        config_path.chmod(0o600)
        return {"codex": config_path}

    def mounts(self, files: dict[str, Path]) -> list[str]:
        return [
            "-v", f"{files['codex'].resolve()}:/home/agent/.codex/config.toml:ro,Z"
        ]

    def build_turn_command(
        self, container: str, config: ControllerConfig, prompt: str,
        session_id: str | None, first_turn: bool,
    ) -> list[str]:
        # Non-interactive exec otherwise cancels MCP calls that would have asked
        # for approval. The filesystem sandbox remains enabled and the container
        # exposes only the authenticated MCP gateway port.
        command = ["podman", "exec", container, "codex", "-a", "never", "exec"]
        if not first_turn and session_id:
            command += ["resume", "--json", "--skip-git-repo-check"]
            if config.model not in {"auto", "default"}:
                command += ["-m", config.model]
            command += [session_id, prompt]
        else:
            command += [
                "--json", "--skip-git-repo-check", "-s", "workspace-write",
                "--color", "never",
            ]
            if config.model not in {"auto", "default"}:
                command += ["-m", config.model]
            command += [prompt]
        return command

    def parse_output(self, stdout: str) -> ControllerTurnOutput:
        result = ControllerTurnOutput(usage={})
        responses: list[str] = []
        for line in stdout.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if value.get("type") == "thread.started":
                result.session_id = value.get("thread_id")
            elif value.get("type") == "item.completed":
                item = value.get("item", {})
                if item.get("type") == "agent_message" and item.get("text"):
                    responses.append(item["text"])
            elif value.get("type") == "turn.completed":
                result.usage = value.get("usage", {})
        result.response_text = "\n".join(responses)
        return result


def backend_for(harness: str) -> ControllerBackend:
    if harness == "codex":
        return CodexControllerBackend()
    if harness in {"claude", "claude.vertex"}:
        return ClaudeControllerBackend()
    raise ValueError(f"unsupported controller harness: {harness}")
