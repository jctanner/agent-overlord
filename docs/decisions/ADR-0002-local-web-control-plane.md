# ADR-0002: Use a Local Web Interface over a Persistent Control-Plane Service

## Status

Accepted

## Context

The Textual MVP proved the usefulness of a live agent inventory, council wall,
worker inspector, and council chat. It also exposed a mismatch between the
terminal presentation layer and the workload.

The interface contains three independently active surfaces:

- A fleet table that changes as agent panes are discovered and reclassified.
- A high-volume wall that continuously appends events.
- An interactive chat composer that must accept input reliably at all times.

In a TUI these surfaces share a terminal render and input channel. Work to reduce
polling cost, suppress unchanged redraws, isolate inventory on another event loop,
and correct keyboard bindings improved the implementation but did not make the
experience consistently pleasant. Frequent activity still competes for visual
attention, terminal focus and selection are fragile, and diagnosing whether input
behavior originates in Textual, tmux, SSH, or the terminal emulator is costly.

This is an architectural lesson from the MVP rather than merely another TUI
performance defect. The monitoring and orchestration core should remain active
independently of any particular presentation client.

## Decision

Separate Agent Overlord into:

1. A persistent Python control-plane service that owns inventory,
   reconciliation, SQLite state, wall events, council behavior, memories, and
   tmux/SSH transports.
2. A local React and TypeScript web client that independently renders the agent
   table, wall, chat, and worker details.

The Python service will use FastAPI and initially bind only to loopback. It will
provide a small HTTP command/query API and a server-sent events stream. SSE is the
default live transport because inventory, wall, and council updates are primarily
server-to-client. WebSockets are not required unless a later interaction cannot
be represented cleanly with HTTP requests and SSE.

The web client will use React, TypeScript, and Vite. Table, wall, and chat state
must update independently so wall activity and inventory refreshes cannot disturb
the chat composer.

Development will run FastAPI and Vite as separate processes managed together by
Honcho from a repository-root `Procfile`. Honcho will be installed through the uv
development dependency group, and `uv run honcho start` will be the documented
full-stack development command.

Normal use will not require a Node process. Vite will build static assets that the
FastAPI process serves alongside the API and SSE endpoints, leaving one persistent
Python runtime process.

The existing Python domain models, configuration, discovery, classification,
storage, inventory, council, and memory services should be retained and improved
rather than rewritten. The Textual client may remain temporarily as a diagnostic
or compatibility client, but it is no longer the primary interface and should not
constrain the service architecture.

Development remains local and self-contained. This decision does not introduce a
publicly reachable service, remote account system, or cloud deployment.

## Consequences

Positive:

- Inventory and orchestration continue when the browser reloads or closes.
- Table refresh, wall append, and chat input have independent rendering paths.
- Browser text input, selection, scrolling, copy/paste, accessibility, and worker
  details are more reliable and familiar.
- The service boundary makes operational state and command authority explicit.
- Future alternate clients can reuse the same API and event model.
- Existing Python control-plane code remains valuable.

Negative:

- The project now has Python and TypeScript toolchains.
- Development uses a small process supervisor and must preserve readable,
  distinguishable logs from the API and Vite processes.
- Service lifecycle, API compatibility, CORS, and error semantics must be
  designed and tested.
- Even a loopback service needs explicit origin and command authorization rules.
- Frontend builds and dependencies add repository and release complexity.
- The UI and service can fail independently and need visible connection-state
  handling.

## Supersedes

This ADR supersedes the Textual presentation and same-process UI/control-loop
portions of ADR-0001. It does not supersede ADR-0001's Python, uv, SQLite,
Pydantic, or system tmux/SSH decisions.
