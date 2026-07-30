from agent_overlord.config import HostConfig
from agent_overlord.transports.tmux import LocalTmuxTransport, SshTmuxTransport


async def test_local_bounded_input_targets_configured_socket_and_pane() -> None:
    transport = LocalTmuxTransport(
        HostConfig(name="local", local=True, tmux_socket="overlord-test")
    )
    commands: list[list[str]] = []

    async def execute(command: list[str], timeout: float) -> str:
        commands.append(command)
        return ""

    transport._execute = execute
    await transport.run_tmux("send-keys", "-t", "%9", "-l", "y")
    assert commands == [[
        "tmux", "-L", "overlord-test", "send-keys", "-t", "%9", "-l", "y"
    ]]


async def test_remote_bounded_input_uses_same_tmux_operation_over_system_ssh() -> None:
    transport = SshTmuxTransport(
        HostConfig(
            name="remote", ssh="user@example.test", port=2222,
            key="/tmp/read-only-key", tmux_socket="agents",
        )
    )
    commands: list[list[str]] = []

    async def execute(command: list[str], timeout: float) -> str:
        commands.append(command)
        return ""

    transport._execute = execute
    await transport.run_tmux("send-keys", "-t", "%4", "Enter")
    assert commands == [[
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-p", "2222",
        "-i", "/tmp/read-only-key", "user@example.test",
        "tmux -L agents send-keys -t %4 Enter",
    ]]
