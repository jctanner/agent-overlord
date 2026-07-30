# ADR-0008: Frontend Access to Raw Council Controller Logs

## Status

Proposed

## Context

Council controller logs are written to append-only files in
`controller-logs/{controller_id}.log` alongside the SQLite database. Each entry
records the full command, duration, exit code, stdout (stream-JSON from the
harness), and stderr. These logs are the primary diagnostic surface for
understanding what a controller actually did during a turn — thinking blocks,
MCP tool calls, token usage, and errors.

Currently there is no way to view these logs from the frontend. The only
visibility into controller behavior is through wall events (lifecycle state
changes) and structured outputs (interpretations, reviews, proposals). When a
controller misbehaves, times out, or produces an unexpected verdict, diagnosing
the cause requires SSH access to the host and manual inspection of the log file.

The SSE broadcast infrastructure is mature and already streams wall events,
controller state, prompts, and other live data to the frontend.

## Decision

Expose raw controller logs to the frontend through two mechanisms:

### REST endpoint for log history

Add `GET /api/controllers/{controller_id}/logs` that reads the controller's log
file and returns recent entries. Support a `tail` query parameter (default 50
lines) to limit output, since log files are append-only with no rotation and can
grow large.

### SSE stream for live log tailing

Add a new SSE event type `controller_log` to the existing broadcast
infrastructure. When `_append_log` writes a new entry, it also publishes the
entry through the broadcaster so connected clients receive it in real time
without polling.

### Frontend log viewer

Add a log viewer accessible from the controller health indicators or the council
audit panel. The viewer should:

- Display raw log output in a scrollable monospace pane.
- Auto-scroll to follow new entries when tailing.
- Allow selecting which controller to view (operator, auditor, strategist).
- Render as a modal consistent with the existing Memories, Approval Center, and
  Worker Inspector modals.

## Consequences

Positive:

- Controller diagnostics are accessible without host SSH access.
- Live tailing makes it possible to watch a controller's turn in progress.
- The REST endpoint supports after-the-fact debugging of completed or failed
  turns.
- Reuses the existing SSE broadcast and modal UI patterns.

Negative:

- Log files contain raw harness output including thinking blocks, which can be
  verbose. The tail parameter mitigates this for the REST endpoint, but live
  tailing of a long turn will produce substantial output.
- Log files have no rotation or size limits. The REST endpoint must handle
  large files gracefully (read from the end, not load the entire file).
- Controller logs may contain sensitive project context from worker pane
  captures. The frontend is already loopback-only, so this does not expand the
  access boundary, but it does make the content more readily visible.
