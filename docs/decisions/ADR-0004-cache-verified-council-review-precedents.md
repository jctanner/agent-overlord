# ADR-0004: Cache Verified Council Decisions as Exact Review Precedents

## Status

Accepted

## Context

Auto yes can answer prompts immediately when deterministic classification
establishes that an operation is routine or elevated. Genuinely unknown
operations require semantic review: first by the fast reviewer and, when that
review is inconclusive, by the full council. This preserves a conservative
authority boundary but makes repeated, identical prompts unnecessarily slow and
expensive.

Council controllers use persistent harness sessions and may remember earlier
work conversationally. That memory is useful context, but it is not a safe
authorization mechanism. It is not guaranteed to be retrieved, has no exact
scope or expiry, cannot prove that a prior action succeeded, and cannot be
audited as a deterministic input to pane control.

Prompt records already retain the structured operation, scope, semantic
decision, reviewer source, and pre/post action evidence in SQLite. A successfully
executed council decision can therefore serve as a durable precedent without
granting controllers direct tmux authority or treating model memory as policy.

## Decision

Agent Overlord will reuse a prior fast- or full-council approval as a review
precedent only for an exact, recently verified match in an Auto-yes-enabled
worker pane.

A precedent match requires equality of:

- normalized command arguments;
- project;
- host;
- harness;
- prompt type;
- risk classification; and
- the semantic response choice available on the current prompt.

The source prompt must:

- have a final `succeeded` status;
- contain an `allow` decision from the `fast` or `council` review tier;
- have both pre-action and post-action fingerprints;
- show a changed fingerprint after execution; and
- remain within `review_precedent_ttl_secs`, which defaults to seven days.

High-risk prompts never use review precedents. Empty or unparseable command
arguments do not qualify. Similar commands are not automatic matches; a near
match may inform a future reviewer, but it grants no pane-input authority.

When a precedent matches, the new prompt records `review_precedent` as its
decision source and cites the source prompt in its rationale. Execution still
passes through the single prompt action arbiter, including live pane recapture,
worker and pane identity checks, prompt fingerprint comparison, semantic-choice
mapping, scoped disables, global pause, rate limiting, and post-action
verification. Cache reuse skips model review, not the action safety boundary.

Verified human, fast-review, full-council, and precedent-backed outcomes may
contribute to a candidate approval policy after three matching successes. A
candidate remains non-authorizing until explicitly activated, and existing
policy risk restrictions continue to apply.

Fast-review timeout, missing typed decisions, explicit escalation, or an
ambiguous allow choice promotes the live prompt to full council review. Only an
inconclusive or failed full council falls through to human review. This tier
promotion is independent of precedent matching but ensures that a cache miss
does not stop prematurely at the fast reviewer.

The precedent store is the existing durable prompt history rather than a second
cache table. This keeps the decision, execution evidence, provenance, expiry
calculation, and audit history in one record model.

## Consequences

Positive:

- Exact repeat operations can proceed without another model turn.
- Reuse is deterministic, scoped, expiring, and auditable.
- Controller conversational memory remains advisory rather than authoritative.
- Every reused approval retains live recapture and post-action verification.
- A failed fast reviewer now falls through to full council instead of requiring
  immediate human intervention.
- Repeated verified outcomes can surface policy candidates without silently
  broadening authority.

Negative:

- Exact matching deliberately misses harmless variations in paths, flags, or
  command formatting.
- Prompt history lookup grows with retained history and may eventually require
  a dedicated indexed projection.
- A previously safe command can change behavior because of external state even
  when its argv is identical; TTL, scope matching, risk exclusion, and live
  verification reduce but do not eliminate that risk.
- Seven days is an operational default rather than a universal safety constant
  and may need per-project or per-risk tuning.
- Invalidating a single precedent before TTL expiry currently requires changing
  its durable record or disabling automation for the relevant scope; a dedicated
  revocation interface may be needed.

## Rejected Alternatives

### Rely on persistent controller memory

Rejected because conversational recall is probabilistic, weakly scoped, and not
an auditable authorization record.

### Automatically create an active policy after one council approval

Rejected because one reviewed operation should not silently become durable
authority, especially when the operation was initially unclassified.

### Match command prefixes or semantic similarity automatically

Rejected for the initial implementation because small suffix changes and shell
control tokens can materially change effects. Broader reuse requires a separate
decision with explicit constraints and evaluation evidence.

### Never reuse review decisions

Rejected because it repeatedly spends council latency and tokens on identical,
already verified work without improving the action-time safety checks.

