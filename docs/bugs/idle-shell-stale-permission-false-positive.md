# Bug: Completed Agent Pane Is Misclassified as Awaiting Permission

## Summary

The `docs-pipeline-busted.2` pane completed its Claude workflow and returned to a
normal Bash prompt, but Agent Overlord continued to classify it as
`awaiting_input` with input kind `permission`.

This false positive generated unnecessary council work and caused the operator
to report a nonexistent permission dialog. The auditor had to correct the result
using current process and terminal evidence.

## Observed Evidence

- `current_command` was `bash`.
- `descendant_commands` was empty, so no Claude process remained below the pane
  shell.
- The final visible line was a normal shell prompt.
- Captured workflow output reported `terminal_reason: "completed"`.
- Older JSON log text still contained words that matched permission patterns.

## Cause

Input detection searches recent captured text for prompt and configured
attention patterns before considering whether an agent process is still alive.
The state classifier then gives `awaiting_input` precedence over its idle-shell
rule. Historical permission language can therefore override stronger current
evidence that the pane has returned to its shell.

## Expected Behavior

A foreground shell with no descendant processes is an idle pane, even when its
retained transcript contains permission language. A previously known agent pane
must remain in inventory with its durable harness identity and purpose, but its
state must be:

- `state: idle`
- `awaiting_input: false`
- `input_kind: null`

A Bash pane with a live wrapped agent descendant remains eligible for normal
input-prompt detection.

## Verification Criteria

- A regression fixture containing stale permission language followed by a shell
  prompt is classified as idle.
- Its known Claude harness identity is retained.
- A Bash pane with a `claude.vertex` descendant can still be classified as
  awaiting input.
- Existing active Codex permission detection continues to pass.

## Resolution

Input classification now treats a foreground shell with no descendants as
conclusive live evidence that the wrapped agent has exited. In that case it
suppresses both built-in input patterns and configured attention patterns before
state classification. Harness classification remains sticky through the
previous worker record, so the completed agent session remains inventoried as
idle.

The suppression does not apply when the shell has descendants. Wrapped Claude
and Codex processes therefore retain normal permission-prompt detection.

## Status

Fixed
