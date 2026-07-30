# Task: Fix Confirmation Classifier False Positives

## Goal

Prevent ordinary agent narration containing words such as `confirm` from
classifying an actively running pane as awaiting input.

## Context

AEM-EMU was running pytest but was reported as awaiting input because the
confirmation input pattern matched an incidental sentence about confirming test
results. Prompt detection must require evidence shaped like an actionable
confirmation rather than an unanchored keyword.

## Acceptance Criteria

- [x] Ordinary narration containing `confirm` does not imply awaiting input.
- [x] Real confirmation prompts remain detected.
- [x] Regression tests cover both cases.

## Status

Done

## Evidence

- Confirmation matching is now shaped like an actionable terminal prompt and
  takes precedence over the generic trailing-question heuristic.
- `env UV_CACHE_DIR=/tmp/ao-uv-cache uv run python -m pytest
  tests/test_classifier.py tests/test_prompts.py -q` — 38 passed.
- `env UV_CACHE_DIR=/tmp/ao-uv-cache uv run python -m pytest -q` — 70 passed.
