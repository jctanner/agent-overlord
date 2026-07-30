from agent_overlord.config import HostConfig
from agent_overlord.services.discovery import FIELD_SEPARATOR, TmuxDiscovery


def test_parse_structured_tmux_row() -> None:
    host = HostConfig(name="remote", ssh="user@host")
    line = FIELD_SEPARATOR.join(
        [
            "$1",
            "session",
            "@2",
            "3",
            "window",
            "%4",
            "1",
            "Codex",
            "/work/repo",
            "codex",
            "codex --model gpt-5",
            "4321",
            "0",
            "1",
            "99",
            "120",
            "40",
        ]
    )
    observation = TmuxDiscovery._parse_row(host, line)
    assert observation.host == "remote"
    assert observation.display_name == "session:window.1"
    assert observation.current_path == "/work/repo"
    assert observation.pane_active is True
    assert observation.worker_id == TmuxDiscovery._parse_row(host, line).worker_id


async def test_capture_keeps_physical_rows_bounded(config) -> None:
    discovery = TmuxDiscovery(config)

    class FakeTransport:
        async def run_tmux(self, *args: str) -> str:
            assert "-J" not in args
            return "short row\n" + ("x" * 5000) + "\n"

    rows = await discovery._capture(FakeTransport(), "%1")
    assert rows[0] == "short row"
    assert len(rows[1]) == 4096


def test_maps_process_descendants_to_their_tmux_pane() -> None:
    children = {
        108783: [(489034, "/bin/bash /home/jtanner/bin/claude.vertex")],
        489034: [(489035, "/home/jtanner/.local/bin/claude")],
        489035: [(489061, "python -m assistant_mcp")],
        999: [(1000, "unrelated")],
    }

    assert TmuxDiscovery._descendant_commands(108783, children) == [
        "/bin/bash /home/jtanner/bin/claude.vertex",
        "/home/jtanner/.local/bin/claude",
        "python -m assistant_mcp",
    ]
