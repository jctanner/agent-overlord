# Agent Overlord Plan

## Implemented Plans

- [MVP: Observe and understand the agent fleet](docs/plans/001-mvp.md)
- [Local web control plane and interface](docs/plans/002-local-web-interface.md)

## Proposed Plans

- [Persistent semantic control council](docs/plans/003-persistent-semantic-council.md)

## Notes

- [Exploratory session notes](docs/notes/session-log.md)

## Active Tasks

No tasks are currently active.

## Completed Tasks

- [Implement the observation-first MVP](docs/tasks/done/implement-observation-mvp.md)

## Open Bugs

- [Whole-pane fingerprint causes false prompt staleness](docs/bugs/open/pane-fingerprint-false-staleness.md)

## Fixed Bugs

- [Council chat Enter appears ignored](docs/bugs/fixed/council-chat-enter-ignored.md)
- [Chat keystrokes trigger global actions](docs/bugs/fixed/chat-keystrokes-trigger-global-actions.md)
- [Sessions panel contains a large empty area](docs/bugs/fixed/sessions-panel-empty-space.md)
- [Inventory work starves chat input](docs/bugs/fixed/inventory-starves-chat-input.md)
- [Screenshot classification and council routing problems](docs/bugs/fixed/screenshot-classification-and-council-routing.md)
- [Inventory shares the Textual event loop](docs/bugs/fixed/inventory-shares-textual-event-loop.md)

## Decisions

- [ADR-0001: Build the MVP as a self-contained Python Textual application](docs/decisions/ADR-0001-python-textual-mvp.md)
- [ADR-0002: Use a local web interface over a persistent control-plane service](docs/decisions/ADR-0002-local-web-control-plane.md)
- [ADR-0005: Replace session detail drawer with modal overlay](docs/decisions/ADR-0005-session-detail-modal.md)
- [ADR-0006: Parallelize council prompt reviews and use a uniform model tier](docs/decisions/ADR-0006-parallel-council-reviews-uniform-model.md)
- [ADR-0007: Agentic work ledger awareness for council controllers](docs/decisions/ADR-0007-ledger-awareness-for-controllers.md)
- [ADR-0008: Frontend access to raw council controller logs](docs/decisions/ADR-0008-frontend-council-log-stream.md)
- [ADR-0009: Single confident reviewer with escalation to full council](docs/decisions/ADR-0009-single-reviewer-with-escalation.md)
