# Plan: Local Web Control Plane and Interface

## Status

Implemented

## Purpose

Replace the Textual MVP as the primary interface with a persistent local Python
control-plane service and a React/TypeScript web client. Preserve the working tmux
inventory, semantic classification, wall, council, memory, and SQLite foundation
while making the interface reliable under continuous fleet activity.

This plan implements
[ADR-0002](../decisions/ADR-0002-local-web-control-plane.md).

## User Outcome

The user can start Agent Overlord once, open a local browser interface, and:

- See the current agent fleet update live.
- Read and filter the wall without disturbing other interface state.
- Type into council chat without inventory or wall refresh affecting input.
- Ask questions and submit instructions to the council.
- Inspect a worker's recent output, interpretation, evidence, and metadata.
- Reload or close the browser without stopping fleet observation.
- Clearly see when the UI loses or regains its connection to the service.

## Target Architecture

```text
Configured local and SSH hosts
              │
              ▼
Python control-plane service
├── tmux discovery and reconciliation
├── semantic worker classification
├── council and shared memory
├── wall/event stream
├── SQLite persistence
├── FastAPI command/query endpoints
└── SSE live event endpoint
              │
              ▼
React + TypeScript web client
├── agent sessions table
├── council wall
├── worker inspector
└── council chat
```

The control-plane service is authoritative. The browser keeps presentation state
such as active selection, filters, scroll/follow mode, and draft chat text.

## Backend Service

Refactor the current application so inventory lifecycle is owned by a persistent
service process rather than the Textual application.

The service should:

- Load the existing tmux-watcher-compatible host configuration.
- Initialize SQLite and recover persisted workers, events, chat, and memories.
- Start and stop the inventory reconciler cleanly with the application lifecycle.
- Continue observing when no browser client is connected.
- Expose typed query and command endpoints.
- Publish meaningful updates to connected SSE clients.
- Preserve the observation-first safety boundary.
- Bind to `127.0.0.1` by default.
- Report host and reconciler health independently of detected agent count.

The backend remains Python 3.12+, uv-managed, Pydantic-based, and testable without
starting a browser.

## Development Process Management

Full-stack development should use Honcho and a repository-root `Procfile` rather
than requiring developers to manage terminals manually.

Honcho should be included in the uv development dependency group. The intended
development command is:

```console
uv run honcho start
```

The Procfile should define independently named API and web processes equivalent
to:

```procfile
api: uv run agent-overlord serve --host 127.0.0.1 --port 8000
web: <frontend-package-command> --prefix web run dev
```

The final web command depends on the frontend package-manager decision. Vite
should proxy `/api` requests and the SSE stream to FastAPI during development so
the browser uses the same relative URLs in development and normal operation.

Honcho is a development convenience, not the production runtime or the owner of
control-plane state. Browser reloads and Vite hot updates must not restart
inventory or reset SQLite state.

## Initial API Surface

The initial API should remain small and task-oriented.

Candidate query endpoints:

- `GET /api/health`
- `GET /api/workers`
- `GET /api/workers/{worker_id}`
- `GET /api/events`
- `GET /api/memories`
- `GET /api/chat`

Candidate command endpoints:

- `POST /api/chat`
- `POST /api/inventory/refresh`
- `POST /api/memories`
- `PATCH /api/memories/{memory_id}`
- `DELETE /api/memories/{memory_id}`

Live updates:

- `GET /api/stream` using `text/event-stream`.

The exact route shapes may change during implementation, but commands must remain
distinct from queries and all payloads should have explicit typed schemas.

## Event Streaming

SSE clients should receive structured events rather than rendered wall strings.
At minimum, live messages should distinguish:

- Worker snapshots or worker additions/changes/removals.
- Wall events.
- Council chat messages.
- Memory changes.
- Host connection and reconciler health.
- Stream readiness and heartbeat events.

Clients must be able to recover after disconnection. The implementation should
use persisted event identifiers or a fresh authoritative snapshot so reconnection
does not silently leave the browser in a partial state.

Slow or disconnected clients must not block inventory reconciliation or other
clients. Event buffers need a defined bound and overflow/recovery behavior.

## Web Client

Build the primary interface with React, TypeScript, and Vite.

The main view retains the useful three-surface concept without forcing them
through one terminal render channel:

### Agent Sessions

- Live table of agent-bearing panes.
- Host, tmux identity, purpose, harness, model, context, and state columns.
- Attention-first sorting with stable selection.
- Clear configured-host and connection-health summary.
- Expandable or side-panel worker inspector.
- Visual distinction for awaiting input, failed, stalled, idle, disconnected,
  active, complete, and unknown states.

### Council Wall

- Append meaningful events without rerendering the sessions table or chat input.
- Follow/pause and jump-to-latest behavior.
- Filters by event kind, host, worker, intent, and actor where data exists.
- Search and expandable structured event details.
- Clear indication of historical versus live events.

### Council Chat

- Stable multiline composer whose draft remains intact during all live updates.
- Enter-to-send behavior with an explicit Send button.
- Visible pending, success, and failure states.
- Links from council answers to referenced workers and wall events.
- Persisted conversation restored after reload.

The browser should display a prominent but non-destructive connection banner when
the service or stream is unavailable and recover automatically when possible.

## State Ownership

Server-owned state:

- Configured hosts and health.
- Worker observations and semantic interpretations.
- Wall events and chat history.
- Shared memories.
- Inventory and council lifecycle.

Client-owned state:

- Selected worker.
- Table sorting and filters.
- Wall follow/pause position and visual filters.
- Open inspector state.
- Unsubmitted chat draft.

The client must not overwrite a draft or move focus because new server state
arrived.

## Service and UI Lifecycle

Development runs FastAPI and Vite as separate Honcho-managed Procfile processes.
The normal-use path should build the frontend and run one FastAPI process that
serves the static assets, API, and SSE stream. Node is a development and build-time
dependency, not a normal runtime process.

The service should shut down cleanly, stop inventory work, and leave SQLite in a
recoverable state. Browser disconnection must not stop or restart inventory.

## Safety and Local Access

- Bind to loopback by default.
- Do not enable permissive CORS for arbitrary origins.
- Validate command payloads through Pydantic schemas.
- Keep automatic tmux input disabled.
- Do not expose raw arbitrary command execution through the API.
- Make any future non-loopback binding an explicit configuration and security
  decision outside this plan.

## Migration Strategy

1. Extract application lifecycle from `OverlordApp` into a UI-independent service.
2. Add FastAPI queries and health reporting over existing services and storage.
3. Add a bounded SSE broadcaster and reconnection behavior.
4. Add Honcho and the root Procfile for the FastAPI and Vite development processes.
5. Build the React shell and read-only sessions table.
6. Add wall streaming, filters, and follow behavior.
7. Add council chat and worker inspection.
8. Add memory management interactions.
9. Build the frontend for serving by FastAPI in normal use.
10. Verify behavior under active inventory and wall load.
11. Make the web interface the documented default.
12. Decide whether to retain the Textual client as a diagnostic tool or remove it.

These steps describe the implemented migration sequence. The Textual client was
retained as a diagnostic fallback.

## Non-Goals

- Public internet exposure.
- Remote user accounts or multi-tenant authorization.
- Mobile-specific design.
- Complex data visualization.
- WebSocket support unless SSE proves insufficient.
- Autonomous permission approval or worker-pane input.
- Rewriting the Python domain and inventory layers in TypeScript.
- Cloud deployment or hosted persistence.

## Acceptance Criteria

- [x] A uv-managed FastAPI service starts inventory independently of any UI
      client.
- [x] A root Procfile and uv-managed Honcho installation start the FastAPI and
      Vite development processes with `uv run honcho start`.
- [x] The service binds to loopback by default and exposes health, workers, worker
      detail, events, chat, and memory operations through typed endpoints.
- [x] A bounded SSE stream publishes worker, wall, chat, memory, and health changes
      without blocking inventory.
- [x] An SSE client can reconnect and recover an authoritative current state.
- [x] Closing or reloading the browser does not interrupt inventory.
- [x] The React/TypeScript client shows the live sessions table, wall, chat, and
      worker inspector.
- [x] Inventory and wall updates do not change chat focus, lose keystrokes, or
      overwrite an unsubmitted draft.
- [x] The wall supports follow/pause, jump to latest, and basic filters.
- [x] Council chat supports Enter and explicit-button submission with visible
      pending and failure states.
- [x] Worker selection remains stable while unrelated workers update.
- [x] Service connection loss and recovery are clearly visible in the browser.
- [x] Persisted workers, wall events, chat, and memories survive service and
      browser restarts.
- [x] The service does not expose arbitrary commands or automatically send input
      to worker panes.
- [x] Backend, frontend, API, stream-reconnection, and active-update interaction
      tests pass through documented commands.
- [x] Normal local startup is documented.
- [x] Normal use serves the built frontend from the FastAPI process and does not
      require a running Node process.

## Resolved Questions

- npm is the standardized frontend package manager and `package-lock.json` is
  authoritative.
- The Textual client remains as a diagnostic fallback, not the primary UI.
- SSE clients receive a 256-item bounded queue and a `resync` signal on overflow;
  reconnecting clients receive a fresh authoritative snapshot.
- Council replies arrive as complete persisted messages in this iteration.
- Loopback binding is sufficient for this local-only iteration; non-loopback
  access requires a later security decision.

## Related Artifacts

- [ADR-0002](../decisions/ADR-0002-local-web-control-plane.md)
- [Observation-first MVP](001-mvp.md)
- [Exploratory session notes](../notes/session-log.md)
