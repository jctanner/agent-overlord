# Task: Expose Auto-Yes Rate-Limit State

## Goal

Make a prompt visibly rate-limited instead of leaving it silently in the
`decided` state after automatic execution is rejected.

## Context

Observatory reached its per-pane action limit after many successful Auto-yes
actions. The arbiter emitted a wall warning but retained a decided prompt with
no error, making the UI appear as though automation had stopped without cause.

## Acceptance Criteria

- [x] Rate-limit rejection is persisted on the prompt.
- [x] The approval UI and session inventory distinguish rate limiting from an
  unprocessed decision.
- [x] A rate-limited prompt remains safe to retry after its window permits.
- [x] Regression tests cover persistence and presentation.

## Status

Done

## Evidence

- The arbiter persists the exact retry timestamp while retaining the safe,
  retryable `decided` state and clears the error after the window opens.
- Session rows now display `rate limited` for affected workers.
- `env UV_CACHE_DIR=/tmp/ao-uv-cache uv run python -m pytest -q` — 70 passed.
- `npm test -- --run` — 10 passed.
- `npm run build` — completed successfully.
