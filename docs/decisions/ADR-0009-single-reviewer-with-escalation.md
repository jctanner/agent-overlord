# ADR-0009: Single Confident Reviewer with Escalation to Full Council

## Status

Accepted

## Context

Council prompt reviews currently require all three controllers (operator,
auditor, strategist) to run sequentially for every council-tier prompt. Most
prompts are straightforward — a confident single reviewer could make the correct
call alone. The full three-controller pipeline adds several minutes of latency
for a consensus that is almost always unanimous.

The existing fast review tier already demonstrates the single-reviewer pattern:
one controller evaluates the prompt and escalates to the full council when it
cannot decide. But fast reviews are limited to elevated-risk prompts on
auto-yes workers. Council-tier prompts (unknown and high risk) always go through
the full pipeline regardless of how clear-cut they are.

## Decision

Replace the all-or-nothing council pipeline with a two-phase escalation:

### Phase 1: Single reviewer

The first controller (operator by default) evaluates the prompt alone. Its
review decision gains a new option alongside allow/deny/escalate:

- **allow** or **deny** with high confidence — the reviewer is certain of
  its judgment. The scheduler accepts the decision immediately without invoking
  the remaining controllers.
- **escalate** — the reviewer is not confident enough to decide alone. This
  triggers Phase 2.

Confidence is expressed by the reviewer's choice of decision type, not a
separate numeric threshold. If the reviewer cannot confidently make its choice,
that is an escalation.

### Phase 2: Remaining controllers (on escalation)

When the first reviewer escalates, the auditor and strategist run — ideally in
parallel per ADR-0006. The full three-controller tally logic then applies as
today: unanimous allow with agreed choice executes, any deny denies, mixed
votes escalate to human.

The first reviewer's findings remain available on the wall for the auditor and
strategist to reference, preserving the benefit of prior analysis.

### High-risk override

High-risk prompts (destructive operations, credentials, deployment) always
require the full council regardless of Phase 1 confidence. The single-reviewer
shortcut applies only to unknown-risk prompts that reach the council tier
because they have no established risk classification.

## Consequences

Positive:

- Most council reviews complete in one controller turn instead of three,
  cutting latency from minutes to under a minute.
- Straightforward prompts (task file moves, read-only commands, standard
  tooling) no longer consume three inference cycles.
- Composes with ADR-0006: when escalation occurs, the remaining two controllers
  run in parallel rather than sequentially.
- The fast review tier becomes a subset of this pattern rather than a separate
  mechanism, potentially simplifying the review flow.

Negative:

- Single-reviewer decisions lack the cross-validation that catches subtle
  errors. Monitoring solo approval accuracy against full-council outcomes will
  indicate whether this trade-off is acceptable.
- The reviewer must accurately self-assess its confidence. An overconfident
  reviewer will approve prompts it should have escalated. The high-risk
  override mitigates this for the most dangerous operations.
- Review precedent caching (ADR-0004) partially overlaps with this
  optimization. Previously-seen prompts already skip the full council via
  cached verdicts. The benefit of ADR-0009 is primarily for novel prompts.

## Supersedes

This ADR refines but does not supersede ADR-0006. Parallel execution of the
remaining controllers during escalation is complementary. ADR-0006's parallel
operator+auditor design applies specifically to the Phase 2 escalation path.
