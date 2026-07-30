# ADR-0006: Parallelize Council Prompt Reviews and Use a Uniform Model Tier

## Status

Proposed

## Context

A full council prompt review currently executes three controller turns
sequentially: operator, then auditor, then strategist. Each turn is a complete
LLM inference cycle with MCP tool calls inside a Podman container. The
strategist runs on Opus while the operator and auditor run on Sonnet, making the
strategist turn the slowest leg of an already additive pipeline.

For a focused prompt decision ("allow this git push?") the total wall-clock time
is the sum of all three turns. In practice this can exceed several minutes,
during which the monitored agent is blocked waiting for approval.

## Decision

### Parallel operator and auditor

Run the operator and auditor turns concurrently using `asyncio.gather` (or
equivalent) instead of the current sequential `for role in target_roles` loop.
Both roles read the same prompt record and pane capture through MCP tools; their
work is independent. The strategist turn still runs after both complete, because
it synthesizes their findings.

The execution order becomes:

1. Operator and auditor run in parallel.
2. Strategist runs after both finish.

Wall-clock time drops from `operator + auditor + strategist` to
`max(operator, auditor) + strategist`.

The per-controller asyncio lock already prevents a single controller from
handling concurrent turns, so this change is safe as long as the operator and
auditor are distinct controller instances.

### Uniform Sonnet model tier

Configure all three controllers to use the same Sonnet model tier. Opus is
stronger at open-ended reasoning but adds significant latency to every council
review. Prompt approval is a bounded, structured task with clear inputs (the
parsed prompt, captured pane output, and prior evidence) and a typed output
(allow/deny/escalate with choice selection). Sonnet is sufficient for this scope
and substantially faster.

This is a configuration default, not a code constraint. Individual deployments
can still override the strategist to Opus for workloads where deeper reasoning
justifies the latency.

## Consequences

Positive:

- Council prompt review wall-clock time is roughly halved: one turn eliminated
  from the critical path by parallelism, and the remaining strategist turn is
  faster on Sonnet.
- Monitored agents spend less time blocked waiting for approval.
- Uniform model simplifies credential and quota management.
- Review precedent caching (ADR-0004) further reduces repeat-prompt latency on
  top of these improvements.

Negative:

- The auditor can no longer reference the operator's wall findings during its
  own turn, since both run concurrently. Each must form an independent
  assessment. This is acceptable for prompt review but may reduce the auditor's
  effectiveness for general worker analysis notifications, where the current
  sequential flow should be preserved.
- Sonnet may produce lower-quality strategic synthesis than Opus on ambiguous or
  novel prompt patterns. Monitoring council escalation-to-human rates after
  the change will indicate whether this is a practical concern.
- The scheduler loop must distinguish between prompt-review notifications
  (parallel-eligible) and other notification types (sequential) to avoid
  breaking the deliberate ordering of general analysis work.
