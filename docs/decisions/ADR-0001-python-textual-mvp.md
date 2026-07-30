# ADR-0001: Build the MVP as a Self-Contained Python Textual Application

## Status

Superseded in part by
[ADR-0002](ADR-0002-local-web-control-plane.md). The Python core, SQLite,
Pydantic, system tmux/SSH transports, and uv decisions remain accepted. The
Textual presentation and single-process UI/control-loop decisions are superseded.

## Context

Agent Overlord needs a live agent-session table, tailing council wall, council
chat, background tmux reconciliation, SQLite persistence, and rapid iteration on
agent classification and memory behavior. Its primary user and managed workers
already operate in terminal and tmux environments. Complex browser visualization
is not expected.

The MVP does not need remote clients or a public service boundary. Introducing a
browser frontend, HTTP API, and separate daemon would add deployment and
distributed-state concerns before the interaction and orchestration model has
stabilized.

## Decision

Implement the MVP as one Python 3.12+ application using:

- Textual for the terminal interface.
- Separate `asyncio` loops inside one process: Textual's UI loop and a dedicated
  inventory loop on a background thread.
- SQLite for durable operational state.
- Pydantic for typed domain and event models.
- System `tmux` and `ssh` commands for host interaction.
- `uv`, a repository-local `.venv`, `pyproject.toml`, and committed `uv.lock` for
  environment and dependency management.

Keep UI widgets, application services, storage, and transports behind internal
typed boundaries. Pass inventory snapshots and wall events to Textual through a
thread-safe in-process queue.

## Consequences

Positive:

- The complete MVP uses one language and one distributable application.
- The interface works locally, inside tmux, and over SSH.
- The system reuses established SSH configuration and authentication behavior.
- The architecture can evolve rapidly while agent semantics remain uncertain.
- A future daemon, HTTP API, or alternate client can reuse the internal service
  and event boundaries.

Negative:

- There is no browser or mobile interface in the MVP.
- Multiple simultaneous UI clients are not supported initially.
- SQLite transactions must remain short and use independent connections safely
  across the UI and inventory threads.
- A future network API will require an explicit authentication and authorization
  design.
