# Task: Implement the Observation-First MVP

## Goal

Execute `docs/plans/001-mvp.md` as a self-contained Python and Textual
application.

## Context

The MVP establishes the observe-understand loop for agent-bearing tmux panes on
the local and configured SSH hosts. It provides the durable inventory, wall,
council chat, and shared memory foundation required for later mixed-model
autonomous control agents.

## Acceptance Criteria

- [x] Host and tmux discovery implemented.
- [x] Stable, persisted semantic worker records implemented.
- [x] Three-panel Textual interface implemented.
- [x] Meaningful, persisted wall events with per-reader cursors implemented.
- [x] Evidence-backed council queries and instruction acknowledgement implemented.
- [x] Shared memory teaching, retrieval, correction, and removal implemented.
- [x] Observation-first safety boundary preserved.
- [x] Automated and live-host verification completed.

## Status

Done

## Evidence

- `uv sync --locked`
- `uv run pytest -q`
- `uv run python -m compileall -q src tests`
- Live read-only inventory against `tmux-watcher/config.yaml`: 12 agent panes,
  three awaiting input, and no host connection errors at verification time.

## Notes

- The MVP council facade is deliberately deterministic and evidence-backed.
  Persistent Claude and Codex control-agent processes remain a future extension at
  this service boundary.
- The implementation uses the system SSH client so existing SSH authentication,
  host-key, proxy, and multiplexing configuration remains authoritative.
