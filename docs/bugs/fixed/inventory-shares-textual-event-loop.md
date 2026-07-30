# Bug: Inventory Shares Textual Event Loop

## Summary

Terminal input remained unreliable because the complete inventory reconciler ran
on Textual's asyncio event loop. Async tmux and SSH waits did not prevent
classification, serialization, SQLite transactions, subprocess creation, and UI
callbacks from competing with keyboard-event processing.

## Resolution

- Run the complete inventory service on a dedicated daemon thread and asyncio
  event loop.
- Pass immutable event and worker snapshots to the TUI through a thread-safe
  queue.
- Drain and coalesce those updates using a lightweight Textual timer.
- Schedule manual refresh requests onto the inventory loop without awaiting them
  in the UI event handler.
- Keep the deployment as one self-contained Python process; no API or daemon is
  introduced.

## Status

Fixed

