from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Markdown

from agent_overlord.domain.workers import Worker


class WorkerDetailScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    CSS = """
    WorkerDetailScreen { align: center middle; }
    #detail-container {
        width: 90%; height: 90%; border: heavy $accent; background: $surface;
        padding: 1 2;
    }
    """

    def __init__(self, worker: Worker) -> None:
        super().__init__()
        self.worker = worker

    def compose(self) -> ComposeResult:
        content = "\n".join(self.worker.observation.content[-80:]) or "(no captured output)"
        evidence = "\n".join(f"- {line}" for line in self.worker.evidence) or "- None"
        markdown = f"""# {self.worker.observation.host}/{self.worker.observation.display_name}

| Field | Value |
|---|---|
| Worker ID | `{self.worker.worker_id}` |
| State | **{self.worker.state.value}** |
| Purpose | {self.worker.purpose} |
| Confidence | {self.worker.confidence:.0%} |
| Harness | {self.worker.harness} |
| Model | {self.worker.model} |
| Context | {self.worker.context} |
| Working directory | `{self.worker.observation.current_path or 'unknown'}` |
| Command | `{self.worker.observation.current_command or 'unknown'}` |
| Pane ID | `{self.worker.observation.pane_id}` |
| Last observed | {self.worker.last_seen_at.isoformat()} |

## Evidence

{evidence}

## Recent pane output

```text
{content.replace('```', '~~~')}
```
"""
        with VerticalScroll(id="detail-container"):
            yield Markdown(markdown)
            yield Footer()

