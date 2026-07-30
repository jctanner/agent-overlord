# Task: Configure an Appropriate Auto-Yes Worker Rate Limit

## Goal

Give explicitly Auto-yes-enabled worker panes a separate configurable hourly
action limit suitable for permission-heavy agent workloads.

## Context

The general safety default of 20 actions per pane per hour is too low for a pane
that the user explicitly opted into Auto yes. Raising the global limit would
weaken protection for every automation source, so the worker opt-in needs a
separate bound.

## Acceptance Criteria

- [x] Automation settings expose a distinct Auto-yes actions-per-worker hourly
  limit.
- [x] The arbiter selects that limit only for worker Auto yes and reviewed
  precedent decisions.
- [x] Existing non-Auto-yes automation retains the general limit.
- [x] Configuration, API types, documentation, and tests are updated.

## Status

Done

## Evidence

- Added `auto_yes_max_actions_per_worker_per_hour`, defaulting to 100, across
  persisted settings, configuration, API and web types, and the Approval Center.
- The arbiter applies it only when the worker is explicitly opted into Auto yes
  and the decision came from Auto yes or its review paths.
- `env UV_CACHE_DIR=/tmp/ao-uv-cache uv run python -m pytest -q` — 70 passed.
- `npm test -- --run` — 10 passed.
- `npm run build` — completed successfully.
