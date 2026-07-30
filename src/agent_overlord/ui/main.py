from __future__ import annotations

import asyncio
import queue
import threading

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, RichLog, Static

from agent_overlord.config import AppConfig
from agent_overlord.domain.events import EventKind, WallEvent
from agent_overlord.domain.workers import Worker, WorkerState
from agent_overlord.services.council import CouncilService
from agent_overlord.services.inventory import InventoryService
from agent_overlord.storage.sqlite import SQLiteStore
from agent_overlord.ui.worker_detail import WorkerDetailScreen


STATE_STYLES = {
    WorkerState.ACTIVE: "green",
    WorkerState.AWAITING_INPUT: "bold yellow",
    WorkerState.IDLE: "dim",
    WorkerState.STALLED: "bold magenta",
    WorkerState.FAILED: "bold red",
    WorkerState.COMPLETE: "cyan",
    WorkerState.DISCONNECTED: "bold red",
    WorkerState.UNKNOWN: "dim yellow",
}

EVENT_STYLES = {
    EventKind.ERROR: "bold red",
    EventKind.WARNING: "yellow",
    EventKind.DISCONNECTED: "bold red",
    EventKind.RECOVERED: "green",
    EventKind.INPUT_REQUESTED: "bold yellow",
    EventKind.HUMAN_MESSAGE: "bold cyan",
    EventKind.COUNCIL_MESSAGE: "blue",
    EventKind.MEMORY: "magenta",
    EventKind.DISCOVERED: "green",
}


class OverlordApp(App[None]):
    TITLE = "Agent Overlord"
    SUB_TITLE = "tmux agent control plane"
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+r", "refresh", "Refresh", priority=True),
        Binding("ctrl+p", "toggle_wall", "Pause wall", priority=True),
        Binding("ctrl+l", "wall_latest", "Wall latest", priority=True),
        Binding("ctrl+f", "cycle_filter", "Wall filter", priority=True),
        Binding("ctrl+g", "focus_chat", "Council chat", priority=True),
        Binding("escape", "focus_sessions", "Sessions"),
    ]

    CSS = """
    Screen { layout: vertical; }
    #summary { height: 1; padding: 0 1; color: $text-muted; }
    #sessions-panel { height: 38%; min-height: 8; border: round $primary; }
    .panel-header { height: 1; min-height: 1; }
    #sessions-title, #wall-title, #chat-title { height: 1; padding: 0 1; text-style: bold; }
    #sessions { height: 1fr; }
    #wall-panel { height: 34%; min-height: 7; border: round $secondary; }
    #wall { height: 1fr; padding: 0 1; }
    #chat-panel { height: 1fr; min-height: 6; border: round $accent; }
    #chat-history { height: 1fr; padding: 0 1; }
    #chat-composer { dock: bottom; height: 3; }
    #chat-input { width: 1fr; }
    #chat-send { width: 10; min-width: 10; }
    .title-meta { text-align: right; width: 1fr; color: $text-muted; }
    """

    def __init__(self, config: AppConfig, store: SQLiteStore) -> None:
        super().__init__()
        self.config = config
        self.store = store
        self.inventory = InventoryService(config, store)
        self.council = CouncilService(store, self.inventory)
        self.worker_records: dict[str, Worker] = {}
        self._worker_render_signature: tuple[tuple[object, ...], ...] = ()
        self.wall_follow = True
        self.wall_filter = "all"
        self._ui_updates: queue.SimpleQueue[tuple[str, object]] = queue.SimpleQueue()
        self._inventory_thread: threading.Thread | None = None
        self._inventory_loop: asyncio.AbstractEventLoop | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Starting inventory…", id="summary")
        with Vertical(id="sessions-panel"):
            with Horizontal(classes="panel-header"):
                yield Label("Agent Sessions", id="sessions-title")
                yield Label("Enter: inspect", classes="title-meta")
            yield DataTable(id="sessions", cursor_type="row", zebra_stripes=True)
        with Vertical(id="wall-panel"):
            with Horizontal(classes="panel-header"):
                yield Label("Council Wall", id="wall-title")
                yield Label("FOLLOW · all", id="wall-mode", classes="title-meta")
            yield RichLog(id="wall", wrap=True, markup=True, auto_scroll=True)
        with Vertical(id="chat-panel"):
            yield Label("Control Council", id="chat-title")
            yield RichLog(id="chat-history", wrap=True, markup=True)
            with Horizontal(id="chat-composer"):
                yield Input(
                    placeholder="Ask or instruct the council… (Enter sends)",
                    id="chat-input",
                )
                yield Button("Send", id="chat-send", variant="primary")
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#sessions", DataTable)
        table.add_columns("Host", "Tmux", "Purpose", "Harness", "Model", "Context", "State")
        await self.store.initialize()
        await self.inventory.initialize()
        self.inventory.on_event(self._receive_event)
        self.inventory.on_workers(self._receive_workers)
        for event in await self.store.list_events(limit=250):
            self._write_wall(event)
        for role, message in await self.store.list_chat_messages():
            self._write_chat(role, message)
        # Tmux/SSH discovery, classification, and persistence intentionally run
        # outside Textual's event loop. The UI consumes only small queued updates.
        self.set_interval(0.05, self._drain_ui_updates)
        self._inventory_thread = threading.Thread(
            target=self._run_inventory_thread,
            name="agent-overlord-inventory",
            daemon=True,
        )
        self._inventory_thread.start()
        table.focus()

    async def on_unmount(self) -> None:
        if self._inventory_loop and self._inventory_loop.is_running():
            self._inventory_loop.call_soon_threadsafe(self.inventory.stop)

    def _run_inventory_thread(self) -> None:
        loop = asyncio.new_event_loop()
        self._inventory_loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.inventory.run())
        except Exception as exc:
            self._ui_updates.put(
                (
                    "event",
                    WallEvent(
                        actor="observer",
                        kind=EventKind.ERROR,
                        severity="error",
                        message=f"Inventory loop stopped: {exc}",
                    ),
                )
            )
        finally:
            loop.close()

    async def _receive_event(self, event: WallEvent) -> None:
        self._ui_updates.put(("event", event))

    async def _receive_workers(self, workers: list[Worker]) -> None:
        # Copy the list before passing it across the thread boundary.
        self._ui_updates.put(("workers", list(workers)))

    def _drain_ui_updates(self) -> None:
        latest_workers: list[Worker] | None = None
        while True:
            try:
                kind, payload = self._ui_updates.get_nowait()
            except queue.Empty:
                break
            if kind == "event":
                self._write_wall(payload)
            elif kind == "workers":
                latest_workers = payload
        # Coalesce multiple inventory snapshots into one table update per tick.
        if latest_workers is not None:
            self._accept_workers(latest_workers)

    def _accept_workers(self, workers: list[Worker]) -> None:
        self.worker_records = {worker.worker_id: worker for worker in workers}
        signature = tuple(
            (
                worker.worker_id,
                worker.observation.host,
                worker.observation.display_name,
                worker.purpose,
                worker.harness,
                worker.model,
                worker.context,
                worker.state,
            )
            for worker in workers
        )
        if signature != self._worker_render_signature:
            self._worker_render_signature = signature
            self._render_workers(workers)

    def _render_workers(self, workers: list[Worker]) -> None:
        table = self.query_one("#sessions", DataTable)
        table.clear()
        for worker in workers:
            state = Text(worker.state.value.upper(), style=STATE_STYLES[worker.state])
            table.add_row(
                worker.observation.host,
                worker.observation.display_name,
                worker.purpose,
                worker.harness,
                worker.model,
                worker.context,
                state,
                key=worker.worker_id,
            )
        attention = sum(worker.awaiting_input for worker in workers)
        failures = sum(
            worker.state in {WorkerState.FAILED, WorkerState.STALLED, WorkerState.DISCONNECTED}
            for worker in workers
        )
        hosts = len(self.config.hosts)
        self.query_one("#summary", Static).update(
            f"{hosts} configured hosts · {len(workers)} workers · {attention} awaiting input · "
            f"{failures} failed/stalled/disconnected"
        )

    def _write_wall(self, event: WallEvent) -> None:
        if self.wall_filter != "all" and event.kind.value != self.wall_filter:
            return
        style = EVENT_STYLES.get(event.kind, "dim")
        time = event.created_at.astimezone().strftime("%H:%M:%S")
        line = Text()
        line.append(time + " ", style="dim")
        line.append(f"{event.actor:<14}", style=style)
        line.append(f" {event.kind.value.upper():<16}", style=style)
        line.append(" " + event.message)
        wall = self.query_one("#wall", RichLog)
        wall.write(line, scroll_end=self.wall_follow)

    def _write_chat(self, role: str, message: str) -> None:
        label = "You" if role == "user" else "Council"
        style = "bold cyan" if role == "user" else "bold blue"
        self.query_one("#chat-history", RichLog).write(
            Text.assemble((f"{label}: ", style), message), scroll_end=True
        )

    @on(Input.Submitted, "#chat-input")
    async def submit_council_input(self, event: Input.Submitted) -> None:
        event.stop()
        await self._submit_chat(event.value)

    @on(Button.Pressed, "#chat-send")
    async def send_council_input(self, event: Button.Pressed) -> None:
        event.stop()
        chat = self.query_one("#chat-input", Input)
        await self._submit_chat(chat.value)

    async def _submit_chat(self, value: str) -> None:
        message = value.strip()
        if not message:
            return
        chat = self.query_one("#chat-input", Input)
        chat.clear()
        self._write_chat("user", message)
        try:
            response = await self.council.handle(message)
        except Exception as exc:
            error = f"Unable to process message: {exc}"
            self._write_chat("council", error)
            await self.inventory.emit(
                WallEvent(
                    actor="council",
                    kind=EventKind.ERROR,
                    severity="error",
                    message=error,
                )
            )
        else:
            self._write_chat("council", response.message)
        finally:
            chat.focus()

    def on_click(self, event) -> None:
        widget = getattr(event, "widget", None)
        if widget is not None and (
            widget.id in {"chat-panel", "chat-title", "chat-history", "chat-composer"}
            or any(ancestor.id == "chat-panel" for ancestor in widget.ancestors)
        ):
            self.query_one("#chat-input", Input).focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        worker = self.worker_records.get(str(event.row_key.value))
        if worker:
            self.push_screen(WorkerDetailScreen(worker))

    def action_refresh(self) -> None:
        if self._inventory_loop and self._inventory_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.inventory.refresh(), self._inventory_loop
            )

    def action_toggle_wall(self) -> None:
        self.wall_follow = not self.wall_follow
        mode = "FOLLOW" if self.wall_follow else "PAUSED"
        self.query_one("#wall-mode", Label).update(f"{mode} · {self.wall_filter}")

    def action_wall_latest(self) -> None:
        self.wall_follow = True
        self.query_one("#wall", RichLog).scroll_end(animate=False)
        self.query_one("#wall-mode", Label).update(f"FOLLOW · {self.wall_filter}")

    async def action_cycle_filter(self) -> None:
        filters = ["all", "input_requested", "warning", "error", "memory"]
        index = (filters.index(self.wall_filter) + 1) % len(filters)
        self.wall_filter = filters[index]
        mode = "FOLLOW" if self.wall_follow else "PAUSED"
        self.query_one("#wall-mode", Label).update(f"{mode} · {self.wall_filter}")
        wall = self.query_one("#wall", RichLog)
        wall.clear()
        for event in await self.store.list_events(limit=500):
            self._write_wall(event)

    def action_focus_chat(self) -> None:
        self.query_one("#chat-input", Input).focus()

    def action_focus_sessions(self) -> None:
        self.query_one("#sessions", DataTable).focus()
