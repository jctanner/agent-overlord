# ADR-0007: Agentic Work Ledger Awareness for Council Controllers

## Status

Proposed

## Context

Most monitored projects follow the agentic work ledger methodology described in
`docs/notes/agentic_work_ledger.md`. The ledger defines a filesystem-native
project management system where tasks are files, status is represented by
directory placement (`pending/`, `current/`, `blocked/`, `done/`), and
operations like `git mv docs/tasks/pending/X.md docs/tasks/done/X.md` are
standard task completion housekeeping.

Council controllers currently have no knowledge of this methodology. When they
encounter ledger-related operations, they lack context to recognize them as
routine workflow. For example, a `git mv` between task directories is classified
as `risk: unknown` because it has no established risk classification, triggering
a full council review for what is effectively project bookkeeping.

Embedding the full ledger document (~260 lines) in every controller system
prompt would waste context budget on turns that do not involve ledger operations.
A durable memory would not be reliably loaded either, since controllers search
memories on demand and may not think to query for project methodology during a
prompt review.

## Decision

Add a short reference to the agentic work ledger in each controller's system
prompt, noting that monitored projects may follow this methodology and directing
controllers to retrieve the full specification when evaluating task lifecycle
operations.

Expose a new `get_agentic_ledger` MCP tool through the controller gateway that
returns the full contents of `docs/notes/agentic_work_ledger.md`. The tool takes
no arguments and serves the current file from disk, so updates to the ledger
document are immediately available to controllers without container rebuilds or
prompt regeneration.

## Consequences

Positive:

- Controllers can recognize ledger operations (task file moves, ADR creation,
  bug filing, PLAN.md updates) as routine project workflow rather than unknown
  risk operations.
- The system prompt stays lean — one sentence of pointer text rather than the
  full document.
- The MCP tool always serves the current version, so ledger updates propagate
  without configuration changes.
- Controllers pull the full reference only when relevant, conserving context
  budget on unrelated turns.

Negative:

- Controllers must decide when to call the tool. If they fail to retrieve the
  ledger before evaluating a task lifecycle prompt, they may still misjudge it.
  The system prompt hint mitigates this by explicitly associating ledger
  operations with the tool.
- The tool serves a single canonical copy from Agent Overlord's own repository
  (`docs/notes/agentic_work_ledger.md`). If the file moves or is renamed, the
  tool must be updated.
