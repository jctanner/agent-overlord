"""Disposable live acceptance for the bounded prompt action path.

Creates an isolated tmux server locally or through system SSH, responds through
PromptActionArbiter, verifies the script consumed the bounded response, and
always removes the isolated tmux server.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_overlord.config import AppConfig, HostConfig
from agent_overlord.domain.prompts import AutomationSettings, PromptDecision
from agent_overlord.services.control_plane import ControlPlane
from agent_overlord.storage.sqlite import SQLiteStore
from agent_overlord.transports.tmux import transport_for


async def run(args: argparse.Namespace) -> None:
    host = HostConfig(
        name="acceptance",
        local=args.ssh is None,
        ssh=args.ssh,
        port=args.port,
        key=args.key,
        tmux_socket=args.socket,
    )
    transport = transport_for(host)
    if args.harness == "claude":
        script = (
            "python3 -c 'import time; print(\"Claude permission\"); "
            "print(\"> uv run pytest -q\"); print(\"Allow this command?\"); "
            "print(\"1. Yes\"); print(\"2. Yes, and don_t ask again\"); "
            "answer=input(\"3. No\\n\"); "
            "print(\"accepted:\"+answer); time.sleep(10)'"
        )
        pane_title = "claude.vertex"
        expected_response = "1"
    else:
        script = (
            "python3 -c 'import time; print(\"Codex permission\"); "
            "print(\"> uv run pytest -q\"); "
            "answer=input(\"Allow command? (y/n)\\n\"); "
            "print(\"accepted:\"+answer); time.sleep(10)'"
        )
        pane_title = "codex"
        expected_response = "y"
    try:
        print(f"creating isolated tmux socket {args.socket}", flush=True)
        await transport.run_tmux(
            "new-session", "-d", "-s", "approval-acceptance", "-n", "prompt", script
        )
        await transport.run_tmux(
            "select-pane", "-t", "approval-acceptance:prompt", "-T", pane_title
        )
        await asyncio.sleep(0.5)
        with TemporaryDirectory(prefix="agent-overlord-acceptance-") as directory:
            config = AppConfig(
                hosts=[host],
                capture_lines=40,
                automation=AutomationSettings(dry_run=False),
            )
            plane = ControlPlane(
                config,
                SQLiteStore(Path(directory) / "acceptance.db"),
                enable_inventory=False,
            )
            await plane.start()
            try:
                print("discovering disposable prompt", flush=True)
                await plane.refresh()
                prompts = await plane.store.list_prompts()
                if len(prompts) != 1:
                    workers = list(plane.inventory.workers.values())
                    detail = [
                        {
                            "title": item.observation.pane_title,
                            "command": item.observation.current_command,
                            "harness": item.harness,
                            "state": item.state,
                            "awaiting": item.awaiting_input,
                            "input_kind": item.input_kind,
                            "tail": item.observation.content[-6:],
                        }
                        for item in workers
                    ]
                    raise RuntimeError(
                        f"expected one live prompt, found {len(prompts)}; workers={detail}"
                    )
                prompt = prompts[0]
                print(f"executing bounded response for {prompt.prompt_id}", flush=True)
                await plane.actions.decide(
                    prompt.prompt_id,
                    PromptDecision.ALLOW,
                    "allow",
                    source="acceptance-test",
                    rationale="disposable bounded-input verification",
                    expected_fingerprint=prompt.observation_fingerprint,
                    expected_worker_id=prompt.worker_id,
                    expected_pane_id=prompt.pane_id,
                )
                result = await plane.actions.execute(prompt.prompt_id)
                if result.status != "succeeded":
                    raise RuntimeError(f"action did not succeed: {result.status}: {result.error}")
                capture = await transport.run_tmux(
                    "capture-pane", "-p", "-t", "approval-acceptance:prompt"
                )
                if f"accepted:{expected_response}" not in capture:
                    raise RuntimeError(
                        "disposable prompt did not consume expected bounded response"
                    )
                print(
                    f"PASS {host.name} socket={args.socket} prompt={prompt.prompt_id} "
                    f"pre={result.pre_action_fingerprint[:8]} "
                    f"post={result.post_action_fingerprint[:8]}"
                )
            finally:
                await plane.stop()
    finally:
        print(f"cleaning isolated tmux socket {args.socket}", flush=True)
        try:
            await transport.run_tmux("kill-server", timeout=5)
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--key")
    parser.add_argument("--socket", default="agent-overlord-acceptance")
    parser.add_argument("--harness", choices=("codex", "claude"), default="codex")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
