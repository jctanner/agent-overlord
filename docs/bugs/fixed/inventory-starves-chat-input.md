# Bug: Inventory Work Starves Chat Input

## Summary

The periodic inventory could monopolize enough application-loop time for terminal
keystrokes to be delayed or lost under a fleet of busy agent panes.

## Cause

Pane capture used `tmux capture-pane -J`. The join option combines wrapped
terminal rows into logical lines. Agent JSON and transcript output could therefore
produce extremely large lines that were repeatedly classified, serialized, and
persisted every two seconds. Pane capture processes were also started without a
concurrency bound, and manual and periodic refreshes could overlap.

## Resolution

- Capture bounded physical terminal rows without `-J`.
- Defensively cap individual captured rows.
- Limit concurrent pane capture subprocesses.
- Serialize inventory refresh cycles.
- Yield to Textual between worker classification units.
- Continue suppressing unchanged table rebuilds and batching SQLite persistence.

## Verification

Automated coverage confirms that capture does not request `-J`, bounds unexpected
line sizes, and preserves the existing chat and inventory behavior.

## Status

Fixed

