from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_overlord.config import AppConfig, ControllerConfig, HostConfig
from agent_overlord.domain.council import ControllerRole, ControllerStatus
from agent_overlord.services.controller_backends import (
    ClaudeControllerBackend,
    CodexControllerBackend,
)
from agent_overlord.services.controller_runtime import (
    CommandResult,
    ControllerContainerPool,
    ControllerTurnTimeout,
)
from agent_overlord.services.inventory import InventoryService
from agent_overlord.storage.sqlite import SQLiteStore


def controller_config(harness: str = "claude.vertex") -> ControllerConfig:
    return ControllerConfig(
        controller_id="operator" if harness.startswith("claude") else "auditor",
        role=ControllerRole.OPERATOR if harness.startswith("claude") else ControllerRole.AUDITOR,
        harness=harness,
        model="sonnet" if harness.startswith("claude") else "default",
        turn_timeout_secs=5,
    )


def app_config(controller: ControllerConfig, **overrides) -> AppConfig:
    return AppConfig(
        hosts=[HostConfig(name="local", local=True)],
        controllers=[controller],
        controller_runtime_enabled=True,
        controller_restart_limit=0,
        **overrides,
    )


def test_backends_generate_restricted_configs_and_resume_commands(tmp_path: Path) -> None:
    claude_config = controller_config()
    claude = ClaudeControllerBackend()
    files = claude.generate_config(
        claude_config, "role prompt", "http://overlord", "secret", tmp_path
    )
    assert files["mcp"].stat().st_mode & 0o777 == 0o600
    assert "Bearer secret" in files["mcp"].read_text()
    initial = claude.build_turn_command("container", claude_config, "work", "uuid", True)
    resumed = claude.build_turn_command("container", claude_config, "work2", "uuid", False)
    assert "--session-id" in initial
    assert resumed[resumed.index("--resume") + 1] == "uuid"
    assert resumed[resumed.index("--allowedTools") + 1] == "mcp__overlord__*"

    codex_config = controller_config("codex")
    codex = CodexControllerBackend()
    codex_files = codex.generate_config(
        codex_config, "role prompt", "http://overlord", "secret", tmp_path
    )
    assert codex_files["codex"].stat().st_mode & 0o777 == 0o600
    codex_text = codex_files["codex"].read_text()
    assert 'default_tools_approval_mode = "approve"' in codex_text
    assert "signal_done" in codex_text
    command = codex.build_turn_command("container", codex_config, "work", "thread", False)
    assert command[3:8] == ["codex", "-a", "never", "exec", "resume"]
    assert command[-2:] == ["thread", "work"]
    assert "-s" not in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command


@pytest.mark.asyncio
@pytest.mark.parametrize("harness", ["claude.vertex", "codex"])
async def test_pool_keeps_warm_container_and_resumes_native_session(
    tmp_path: Path, harness: str
) -> None:
    commands: list[list[str]] = []

    async def run(command: list[str], _timeout: float) -> CommandResult:
        commands.append(command)
        if "exec" not in command:
            return CommandResult(0)
        if harness == "codex":
            output = [
                {"type": "thread.started", "thread_id": "thread-1"},
                {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
            ]
        else:
            session = command[command.index("--session-id") + 1] if "--session-id" in command else "session-1"
            output = [{"type": "result", "result": "done", "session_id": session}]
        return CommandResult(0, "\n".join(json.dumps(item) for item in output))

    controller = controller_config(harness)
    store = SQLiteStore(tmp_path / "runtime.db")
    await store.initialize()
    inventory = InventoryService(app_config(controller), store)
    await inventory.initialize()
    pool = ControllerContainerPool(
        app_config(controller), store, inventory, {controller.controller_id: "token"},
        run_command=run,
    )
    await pool.start()
    await pool.run_turn(controller.controller_id, "first")
    first_session = pool.states[controller.controller_id].session_id
    await pool.run_turn(controller.controller_id, "second")
    exec_commands = [command for command in commands if "exec" in command]
    assert len([command for command in commands if "run" in command]) == 1
    assert first_session
    assert pool.states[controller.controller_id].cycles_completed == 2
    if harness == "codex":
        assert exec_commands[1][3:8] == ["codex", "-a", "never", "exec", "resume"]
        assert exec_commands[1][-2] == "thread-1"
    else:
        assert exec_commands[1][exec_commands[1].index("--resume") + 1] == first_session
    launch = next(command for command in commands if "run" in command)
    launch_text = " ".join(launch)
    assert "tmux" not in launch_text
    assert ".ssh" not in launch_text
    assert str(Path.cwd()) not in launch_text
    assert "pasta:-T,8001" in launch
    assert launch[launch.index("--network") + 1] != "host"
    await pool.stop()


@pytest.mark.asyncio
async def test_per_controller_lock_serializes_turns_and_timeout_resets_session(
    tmp_path: Path,
) -> None:
    active = 0
    maximum_active = 0
    calls = 0

    async def run(command: list[str], _timeout: float) -> CommandResult:
        nonlocal active, maximum_active, calls
        if "exec" not in command:
            return CommandResult(0)
        calls += 1
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        if calls == 3:
            return CommandResult(-1, stderr="timeout")
        return CommandResult(0, json.dumps({"type": "result", "result": "ok", "session_id": "s"}))

    controller = controller_config()
    config = app_config(controller)
    store = SQLiteStore(tmp_path / "locked.db")
    await store.initialize()
    inventory = InventoryService(config, store)
    await inventory.initialize()
    pool = ControllerContainerPool(config, store, inventory, {"operator": "token"}, run_command=run)
    await pool.start()
    await asyncio.gather(pool.run_turn("operator", "one"), pool.run_turn("operator", "two"))
    assert maximum_active == 1
    with pytest.raises(ControllerTurnTimeout):
        await pool.run_turn("operator", "timeout")
    assert pool.states["operator"].status == ControllerStatus.FAILED
    assert pool.states["operator"].session_id is None
    recovered = await pool.run_turn("operator", "recover from authoritative MCP state")
    assert recovered.response_text == "ok"
    assert pool.states["operator"].status == ControllerStatus.READY
    assert pool.states["operator"].session_id == "s"
    await pool.stop()


@pytest.mark.asyncio
async def test_pool_removes_all_controller_containers_in_one_shutdown_command(
    tmp_path: Path,
) -> None:
    controllers = [
        ControllerConfig(
            controller_id=controller_id, role=role, harness="claude.vertex",
            model="sonnet",
        )
        for controller_id, role in (
            ("operator", ControllerRole.OPERATOR),
            ("auditor", ControllerRole.AUDITOR),
            ("strategist", ControllerRole.STRATEGIST),
        )
    ]
    config = AppConfig(
        hosts=[HostConfig(name="local", local=True)], controllers=controllers,
        controller_runtime_enabled=True, controller_restart_limit=0,
    )
    commands: list[tuple[list[str], float]] = []

    async def run(command: list[str], timeout: float) -> CommandResult:
        commands.append((command, timeout))
        return CommandResult(0)

    store = SQLiteStore(tmp_path / "grouped-stop.db")
    await store.initialize()
    inventory = InventoryService(config, store)
    await inventory.initialize()
    pool = ControllerContainerPool(
        config, store, inventory,
        {item.controller_id: "token" for item in controllers}, run_command=run,
    )
    await pool.start()
    commands.clear()

    await pool.stop()

    assert commands == [(
        [
            "podman", "rm", "-f", "agent-overlord-operator",
            "agent-overlord-auditor", "agent-overlord-strategist",
        ],
        3,
    )]
    assert all(
        state.status == ControllerStatus.STOPPED for state in pool.states.values()
    )


@pytest.mark.asyncio
async def test_cancelling_host_command_terminates_its_subprocess(tmp_path: Path) -> None:
    controller = controller_config()
    config = app_config(controller)
    store = SQLiteStore(tmp_path / "cancel-command.db")
    await store.initialize()
    inventory = InventoryService(config, store)
    await inventory.initialize()
    pool = ControllerContainerPool(
        config, store, inventory, {controller.controller_id: "token"}
    )
    task = asyncio.create_task(pool._execute(["sleep", "30"], 30))
    await asyncio.sleep(0.02)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
