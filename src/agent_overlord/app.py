from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from platformdirs import user_data_path
import uvicorn

from agent_overlord.api.server import create_app
from agent_overlord.config import AppConfig
from agent_overlord.services.control_plane import ControlPlane
from agent_overlord.services.inventory import InventoryService
from agent_overlord.storage.sqlite import SQLiteStore
from agent_overlord.ui.main import OverlordApp


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Observe tmux-hosted agent sessions")
    result.add_argument(
        "mode", nargs="?", choices=("serve", "tui"), default="serve",
        help="run the local web service (default) or legacy Textual client",
    )
    result.add_argument("--config", type=Path, help="tmux-watcher-compatible YAML config")
    result.add_argument("--database", type=Path, help="SQLite database path")
    result.add_argument("--once", action="store_true", help="inventory once and print JSON")
    result.add_argument("--host", default="127.0.0.1", help="web bind host")
    result.add_argument("--port", type=int, default=8000, help="web bind port")
    result.add_argument("--static-dir", type=Path, default=Path("web/dist"))
    return result


def default_config_path() -> Path:
    candidates = (Path("config.yaml"), Path("tmux-watcher/config.yaml"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "No config.yaml found; pass --config with a tmux-watcher-compatible file"
    )


async def run_once(config: AppConfig, store: SQLiteStore) -> None:
    await store.initialize()
    inventory = InventoryService(config, store)
    await inventory.initialize()
    workers = await inventory.refresh()
    payload = {
        "workers": [json.loads(worker.model_dump_json()) for worker in workers],
        "host_errors": inventory.host_errors,
    }
    print(json.dumps(payload, indent=2))


def main() -> None:
    args = parser().parse_args()
    config_path = args.config or default_config_path()
    database = args.database or user_data_path("agent-overlord") / "overlord.db"
    config = AppConfig.load(config_path)
    store = SQLiteStore(database)
    if args.once:
        asyncio.run(run_once(config, store))
        return
    if args.mode == "tui":
        OverlordApp(config, store).run()
        return
    control_plane = ControlPlane(config, store)
    web_app = create_app(control_plane, static_dir=args.static_dir)
    uvicorn.run(web_app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
