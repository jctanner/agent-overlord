from pathlib import Path

from textual.widgets import DataTable, Input, Label, RichLog

from agent_overlord.services.classifier import WorkerClassifier
from agent_overlord.storage.sqlite import SQLiteStore
from agent_overlord.ui.main import OverlordApp


async def test_tui_renders_three_panels_and_worker(
    tmp_path: Path, config, codex_observation
) -> None:
    store = SQLiteStore(tmp_path / "ui.db")
    app = OverlordApp(config, store)
    worker = WorkerClassifier(config).classify(codex_observation)

    async def no_background_inventory() -> None:
        await app._receive_workers([worker])
        await app.inventory._stop.wait()

    app.inventory.run = no_background_inventory
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause()
        sessions = app.query_one("#sessions", DataTable)
        assert sessions.row_count == 1
        sessions_panel = app.query_one("#sessions-panel")
        # Border + one-line header: the table should begin immediately beneath
        # the title rather than after a flexible block of empty space.
        assert sessions.region.y - sessions_panel.region.y <= 2
        assert app.query_one("#wall", RichLog)
        await pilot.press("ctrl+p")
        assert "PAUSED" in str(app.query_one("#wall-mode", Label).render())
        await pilot.press("ctrl+l")
        assert "FOLLOW" in str(app.query_one("#wall-mode", Label).render())
        chat = app.query_one("#chat-input", Input)
        await pilot.click("#chat-input")
        await pilot.press("s", "t", "a", "t", "u", "s")
        assert chat.value == "status"
        await pilot.press("enter")
        await pilot.pause()
        assert chat.value == ""
        messages = await store.list_chat_messages()
        assert messages[-2][1] == "status"
        assert messages[-1][0] == "council"

        app.query_one("#sessions", DataTable).focus()
        await pilot.click("#chat-history")
        assert app.focused is chat

        await pilot.click("#chat-input")
        await pilot.press("s", "t", "a", "t", "u", "s")
        await pilot.click("#chat-send")
        await pilot.pause()
        assert (await store.list_chat_messages())[-1][0] == "council"

        # App-wide single-letter bindings must never steal ordinary prose from
        # the focused chat input or trigger expensive inventory actions.
        await pilot.click("#chat-input")
        prose = "please remember preferences for personal projects"
        await pilot.press(*prose)
        assert chat.value == prose
