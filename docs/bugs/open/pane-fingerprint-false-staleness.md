# Bug: Whole-Pane Fingerprint Causes False Prompt Staleness

## Summary

The prompt staleness checks compare `content_fingerprint` (a SHA-256 of the
entire tmux pane output) rather than `prompt_signature` (a SHA-256 of the
prompt type, operation, and choices). When non-prompt content in the pane
scrollback changes — trailing whitespace, cursor artifacts, status bar updates,
line wrapping — the fingerprint changes and the prompt is marked stale even
though the actual prompt is identical.

## Reproduction

1. A worker pane displays a permission prompt (e.g., `git mv ...`).
2. A council review begins. The `observation_fingerprint` is recorded.
3. While the council is deliberating (sequential operator → auditor →
   strategist turns), the pane content shifts slightly without the prompt
   itself changing.
4. The next `observe_workers` cycle sees a different `content_fingerprint`,
   marks the prompt as `STALE` with error `"worker prompt changed or
   disappeared"`, and the council verdict is discarded.
5. A new prompt is created for the same unchanged prompt, and the entire
   council review restarts from scratch.

Observed on arch-context.2: prompt `bafc2784` was marked stale after operator
and auditor had both voted ALLOW, because the pane fingerprint changed from
`30887478...` to `3a1912a0...`. The `prompt_signature` was identical across both
prompt records (`2c08a49f`). The replacement prompt `aa4e0942` required a full
new council review of the same operation.

## Expected

A prompt should only be marked stale when the prompt itself changes (different
operation, choices, or type), not when unrelated pane content shifts.

## Actual

Staleness is determined by whole-pane `content_fingerprint`, which is sensitive
to any change in the pane scrollback.

## Impact

High — council reviews take several minutes due to sequential controller turns.
False staleness discards completed reviews and forces full restarts, effectively
doubling or tripling wall-clock time for unchanged prompts. This is the most
common source of wasted council review work.

## Affected Code

- `prompts.py:293-297` — `observe_workers` visibility check
- `prompts.py:301-303` — `observe_workers` staleness branch
- `prompts.py:555-557` — `request_review` pre-check
- `council_scheduler.py:231-232` — worker-state stale-before-start guard
- `council_scheduler.py:245` — prompt stale-before-start guard

All compare `worker.observation.content_fingerprint` against the stored
`observation_fingerprint`.

The action arbiter pre-action checks in `actions.py` are also affected:

- `actions.py:155` — inventory fingerprint check before recapture
- `actions.py:163` — recaptured fingerprint check before action

These cause auto-approved prompts to fail at execution time even though the
prompt is unchanged. Observed on arch-context.3: the Claude Code UI has a
spinning activity indicator (`●` / space) that toggles every few seconds,
flipping the pane fingerprint between two states. The `ls | grep | head`
prompt (`6c0c8775`, `8ec89650`) was auto-approved as routine but failed
twice at `"recaptured fingerprint changed before action"`.

All check sites should use `prompt_signature` for prompt identity. Post-action
verification (`actions.py:192`) correctly uses fingerprint changes to detect
that the pane responded — that usage is valid and should remain.

## Related Tasks

- ADR-0006 (parallel council reviews) reduces the window for false staleness
  but does not eliminate it.
