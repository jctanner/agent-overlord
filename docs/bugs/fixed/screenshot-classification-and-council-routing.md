# Bug: Screenshot Reveals Classification Flapping and Generic Council Routing

## Summary

A live interface screenshot showed three related accuracy problems:

- A Codex pane was labeled Claude because its transcript discussed both models.
- Quiet known-agent panes repeatedly changed from idle to disconnected and back as
  identifying harness text left the capture window.
- Questions naming a particular session or host returned fleet-wide status or a
  generic acknowledgement.

Idle shell panes were also being labeled stalled after their output remained
unchanged, even though tmux reported an idle foreground shell.

## Resolution

- Harness classification now scores process metadata and harness-specific terminal
  signatures rather than accepting the first incidental model word.
- Model detection is constrained to the classified harness family.
- Known agent panes retain their identity when recent output becomes quiet.
- Foreground shells remain idle rather than aging into stalled.
- Council questions resolve host, session, window, project, purpose, and worker
  terms before falling back to fleet status.
- The summary reports configured hosts, including hosts with no agent panes.

## Verification

Regression tests cover mixed Claude/Codex transcript text, quiet-shell identity,
idle-versus-stalled classification, disconnected-event flapping, session-specific
questions, and host-specific questions.

## Status

Fixed

