# Plan: Tiered Prompt Approval and Safe Pane Input

## Status

Implemented (2026-07-16)

## Purpose

Advance Agent Overlord from observation and recommendation into narrowly scoped,
auditable interaction with agent permission prompts. Routine, previously
understood approvals should complete in seconds without waiting for the full
three-role council. Novel or risky requests should escalate through progressively
more expensive review, with the human remaining the final authority.

This phase implements the action-boundary follow-on deferred by
[Plan 003](003-persistent-semantic-council.md). It does not grant controllers
general terminal access and does not yet implement intent-driven worker creation.

## Problem Statement

The persistent council is intentionally deliberative. A human question currently
takes roughly two to four minutes because operator, auditor, and strategist turns
run sequentially. Live agent permission prompts may disappear or change within
seconds, so routing every yes/no prompt through the full council produces stale
work and cannot keep a fleet moving efficiently.

At the same time, direct unrestricted `tmux send-keys` would give semantic agents
too much authority and would make prompt races dangerous. Approval requires a
server-owned action boundary that binds a reviewed decision to the exact prompt
that was observed and verifies the result after input.

## User Outcome

The user can define an automation posture and let Agent Overlord resolve routine
agent prompts with minimal intervention. The interface shows:

- What each pane is asking.
- The exact command or operation under review.
- Which policy or reviewer made the decision.
- Whether the response was sent, rejected as stale, or escalated.
- The resulting pane state and evidence.
- A queue containing only decisions that genuinely require the user.

The user can pause automation globally, disable it for a host, project, session,
or worker, revoke learned policy, and inspect a complete action history.

## Guiding Decisions

- The FastAPI control plane is the only component allowed to send pane input.
- Controllers propose or review decisions through typed tools; they never receive
  raw tmux or SSH command authority.
- Observation fingerprints bind decisions to exact captured evidence.
- Current prompt verification immediately precedes every response.
- The common path is deterministic and policy-driven.
- Full council deliberation is reserved for exceptional risk or ambiguity.
- Failure to prove freshness or scope results in no action.
- Automatic action outcomes become evidence for policy review, not silent
  permanent authority expansion.

## Decision Pipeline

```text
Detected input prompt
        │
        ▼
Structured prompt extraction
├── worker and pane identity
├── harness and prompt type
├── exact command/operation
├── choices and proposed response
└── observation fingerprint
        │
        ▼
Freshness and eligibility checks
        │
        ▼
Risk classification + scoped policy lookup
        │
        ├── deterministic allow/deny ───────┐
        ├── fast reviewer ──────────────────┤
        ├── full council ───────────────────┤
        └── human decision ─────────────────┤
                                            ▼
                                  Server action arbiter
                                  ├── recapture prompt
                                  ├── compare fingerprint
                                  ├── validate exact choice
                                  ├── send bounded input
                                  └── verify outcome
                                            │
                                            ▼
                                  Audit event + policy feedback
```

## Structured Prompt Model

Add a durable `PromptRequest` record rather than relying only on worker state:

- Prompt ID and worker ID.
- Host, tmux socket, session, window, and pane identity.
- Harness and prompt type.
- Exact normalized operation or command, preserving the original text.
- Available choices and their terminal representations.
- Observation fingerprint and capture timestamp.
- Detection confidence and evidence lines.
- Risk classification and reasons.
- Lifecycle status: detected, evaluating, escalated, decided, executing,
  succeeded, rejected, stale, failed, or expired.
- Decision, decision source, policy ID, reviewer IDs, and timestamps.
- Pre-action and post-action fingerprints.

Prompt extraction must be harness-aware. Claude and Codex prompt formats should
have explicit parsers with an `unknown` fallback. If the exact operation and
choice cannot be extracted reliably, the prompt cannot take the deterministic
execution path.

## Risk and Policy Model

Begin with conservative server-owned risk categories.

### Routine

Candidates for deterministic approval after exact matching:

- Read-only repository inspection.
- Established test commands inside the current project environment.
- Formatting, linting, and type-checking commands with known prefixes.
- Repetition of an exact command previously approved for the same scope.

### Elevated

Require at least a fast semantic reviewer:

- Dependency installation or updates.
- Network access.
- Commands that write broadly within the repository.
- New command variants that resemble, but do not exactly match, known policy.

### High Risk

Require full council review or direct human approval:

- Deployment, publication, release, or external messaging.
- Destructive filesystem or Git operations.
- Credential, secret, authentication, or privilege prompts.
- Infrastructure changes or actions outside the project workspace.
- Any prompt with uncertain parsing, target, effect, or reversibility.

Policies must be typed and scoped. A policy contains:

- Stable policy ID, status, author, and provenance.
- Decision: allow, deny, or escalate.
- Exact command or normalized prefix matcher.
- Harness, host, project, repository, and optional worker/session scope.
- Permitted prompt choices.
- Risk ceiling and required verification.
- Creation, confirmation, expiration, and revocation timestamps.
- Usage count and recent success/failure outcomes.

Prefix policies must compare parsed argument vectors or another structured form,
not raw shell substring containment. Shell operators, redirections, substitutions,
and environment changes must not inherit authority accidentally.

## Review Tiers

### Tier 0: Deterministic Policy

The server evaluates an exact, current prompt against active scoped policy. No
model invocation occurs. The target response should normally be decided within
one inventory interval.

### Tier 1: Fast Reviewer

One configured low-latency controller reviews the prompt, current capture,
operation, risk classification, and applicable policy. It returns a typed allow,
deny, or escalate decision with confidence and evidence. It cannot execute.

The fast reviewer must have a shorter timeout than council turns and should not
perform broad fleet research for a single prompt.

### Tier 2: Full Council

Operator, auditor, and strategist review novel or high-risk prompts. The
scheduler-owned publication principle from
[ADR-0003](../decisions/ADR-0003-scheduler-owned-council-publication.md) also
applies to action decisions: no controller executes during deliberation, and the
arbiter receives only the completed durable decision.

### Tier 3: Human

The user receives an actionable decision card containing the exact operation,
risk explanation, current evidence, council recommendation, and explicit choices.
The choice remains subject to the same recapture and freshness checks when sent.

## Safe Pane Input Boundary

Implement a narrow control-plane operation such as `respond_to_prompt`. Its input
must include:

- Prompt ID.
- Expected worker ID and pane identity.
- Expected observation fingerprint.
- Explicit semantic choice, mapped by the server to a harness-specific response.
- Decision and policy/review provenance.

The operation must:

1. Acquire a per-pane action lock.
2. Recapture the pane through its configured local or SSH transport.
3. Reparse the current prompt.
4. Confirm worker, pane, prompt type, exact operation, choices, and fingerprint
   still match the decision.
5. Reject prompts that changed, disappeared, completed, or became ambiguous.
6. Send only the bounded response sequence associated with the explicit choice.
7. Recapture until a short verification deadline.
8. Record whether the prompt cleared and what state followed.
9. Emit durable wall and audit events for both success and failure.

The initial API must not expose arbitrary key strings, arbitrary tmux targets, or
general shell execution. Harness-specific response mappings should distinguish
Enter, a numbered selection, yes/no text, and “approve this prefix” rather than
assuming all prompts accept the same keystrokes.

## Staleness and Queue Control

Prompt work must supersede by pane and fingerprint. When a pane changes:

- Pending reviews for older fingerprints become stale immediately.
- Duplicate notifications for the same prompt coalesce.
- A resolved or disappeared prompt cancels queued controller work where possible.
- Human questions retain priority over background analysis.
- Routine prompt evaluation does not enter the general semantic council queue.

Before a queued review begins, the scheduler should compare its prompt record to
current inventory and discard stale work. This addresses the notification races
already observed during rapidly changing agent sessions.

## Policy Learning and Memory

The system may propose a reusable policy after repeated successful decisions, but
must not silently broaden authority from a single approval.

- “Approve once” creates no reusable policy.
- “Approve this exact command for this project” creates an explicit scoped policy.
- Repeated human approvals may generate a candidate policy for confirmation.
- Failed, stale, or corrected actions lower confidence and may suspend a policy.
- Council audits can recommend narrowing, expiring, or revoking policy.
- Every automatic action must explain the policy and evidence that authorized it.

Memory may help retrieve prior outcomes, but only active typed policy grants
execution authority.

## Interface

Add an approval surface that does not require watching the busy wall:

- A count of current prompt requests by tier and status.
- Cards showing worker, host, project, operation, age, risk, and proposed choice.
- One-time approve, deny, escalate, and scoped-policy actions.
- Clear “stale” and “prompt changed” outcomes.
- Global automation pause plus scoped disable controls.
- Policy browser with provenance, scope, recent use, and revoke controls.
- Action history showing precondition checks and post-action verification.

The agent sessions table should distinguish genuine live prompts from classifier
attention hints. Council progress should show the active tier and reviewer rather
than only a generic investigating state.

## Configuration

Add explicit defaults such as:

- Automation disabled until enabled by the user.
- Per-host and per-project automation posture.
- Fast-review controller assignment and timeout.
- Council escalation thresholds.
- Prompt expiration and post-action verification deadlines.
- Maximum automatic actions per pane and per time window.
- Dry-run mode that records decisions without sending input.

Start in dry-run mode against the real fleet. Compare proposed decisions to human
choices before enabling any automatic response.

## Delivery Sequence

### Phase 1: Durable Prompt Detection

- Add structured prompt records and harness-aware extraction.
- Separate confirmed live prompts from heuristic attention hints.
- Coalesce and expire prompt records by pane and fingerprint.
- Display prompt records and lifecycle in the web interface.

### Phase 2: Policy and Dry-Run Decisions

- Add typed scoped policies and deterministic risk classification.
- Evaluate routine prompts without model calls.
- Add fast-review and council escalation records.
- Run in dry-run mode and compare decisions with human responses.

### Phase 3: Constrained Manual Execution

- Implement server-owned `respond_to_prompt` with per-pane locks, recapture,
  exact matching, bounded response mappings, and outcome verification.
- Allow only explicit human choices through the UI.
- Exercise local and SSH panes across Claude and Codex prompt formats.

### Phase 4: Scoped Automatic Approval

- Enable Tier 0 execution only for explicit active policies.
- Add rate limits, global pause, scoped disable, expiration, and revocation.
- Audit automatic outcomes and automatically suspend policies on anomalies.

### Phase 5: Reviewed Escalation

- Allow the fast reviewer and full council to produce durable decisions for the
  arbiter.
- Keep high-risk categories human-gated initially.
- Add candidate-policy learning from repeated reviewed outcomes.

## Verification

Automated and live acceptance must demonstrate:

- Completed shell panes containing stale permission language do not create prompt
  records.
- Exact duplicate prompts coalesce and changed prompts supersede old work.
- Stale fingerprints, changed commands, and disappeared prompts never receive
  input.
- Policies cannot expand through shell syntax tricks or neighboring prefixes.
- Controllers cannot call tmux, SSH, or arbitrary key-sending operations.
- Human one-time decisions work locally and remotely for Claude and Codex.
- Post-action capture proves whether the prompt cleared.
- Tier 0 decisions meet a seconds-scale latency target without council work.
- Fast-review and council failures result in escalation or no action.
- Restart recovery does not repeat an already verified action.
- Every decision and execution has complete durable provenance.
- Global pause and scoped disable prevent execution immediately.

Before automatic approval is enabled, a representative dry-run corpus should show
no unsafe approvals and an acceptably low rate of unnecessary escalation.

## Non-Goals

- General remote shell execution.
- Arbitrary terminal typing by controllers.
- Automatically answering credentials or secrets.
- Unattended deployment, publication, or destructive operations.
- Creating new tmux sessions from user intent.
- Replacing project-specific sandbox and permission mechanisms.
- Treating model confidence alone as execution authority.

## Dependencies and Related Artifacts

- [Persistent semantic council](003-persistent-semantic-council.md)
- [ADR-0003: Scheduler-owned council publication](../decisions/ADR-0003-scheduler-owned-council-publication.md)
- [Idle-shell stale-permission bug](../bugs/idle-shell-stale-permission-false-positive.md)
- Existing local and SSH tmux transports.
- Existing observation fingerprints, worker inventory, SQLite store, wall events,
  controller runtime, and council notification scheduler.

## Implementation Evidence

The implementation follows the five delivery phases in this plan.

- Durable `PromptRequest`, `ApprovalPolicy`, and `AutomationSettings` records are
  stored in SQLite. Harness-aware Claude numbered-choice and Codex yes/no parsers
  preserve exact operations, structured argument vectors, bounded responses,
  evidence, fingerprints, risk, tier, decisions, and action outcomes.
- Prompt reconciliation coalesces identical observations, expires old requests,
  stales changed/disappeared prompts, recovers interrupted reviews as human
  escalations, and marks uncertain in-flight actions failed without replaying
  them after restart.
- Tier 0 evaluates only routine prompts against active, scoped exact or argument-
  prefix policies. Shell control syntax cannot inherit prefix authority.
  Elevated, high-risk, credential, unknown, and unparsed operations bypass
  deterministic execution.
- A configured fast reviewer has a separate 60-second default timeout. Fast and
  full-council reviews use typed `get_prompt` and `review_prompt` MCP tools. Full
  review requires a typed decision from every controller that signaled
  completion; disagreement or missing decisions escalates to the human.
- `PromptActionArbiter` is the only pane-write boundary. It uses per-pane locks,
  verifies expected worker, pane, fingerprint, operation, prompt signature, and
  choice, sends only the captured response, and requires the original prompt to
  clear before recording success. It applies global pause, dry-run, host,
  project, session, worker, and per-pane rate controls.
- Automatic execution requires all three conditions: automation enabled, dry run
  disabled, and an active explicit Tier 0 policy. Policy failures suspend the
  policy. Three matching verified one-time human outcomes may create only a
  candidate policy, which grants no authority before explicit activation.
- The web approval center shows confirmed prompts separately from classifier
  attention hints, risk and tier, age, reviewer progress, recommendations,
  exact choices, scoped disables, automation controls, policy provenance and
  revocation, and action history with pre/post fingerprints.

Automated acceptance:

- `uv run python -m pytest -q` passes the backend suite, including the structured
  risk corpus, coalescing, stale rejection, exact/prefix safety, Tier 0 without
  council work, dry-run and pause behavior, restart non-replay, typed review
  completion, policy candidacy, API lifecycle, and local/SSH transport commands.
- `npm --prefix web test -- --run` passes the React interaction suite, including
  approval-center dry-run decisions and scoped controls.
- `npm --prefix web run build` passes TypeScript and the production Vite build.

Live disposable acceptance used `scripts/live_prompt_acceptance.py`. It created
isolated named tmux sockets, discovered a real foreground prompt, passed the
decision through the control plane, consumed the exact bounded response, proved
the prompt cleared with distinct pre/post fingerprints, and removed the socket.
All four transport/format combinations passed:

- Local Codex yes/no: `pre=7eb20c24`, `post=a06e2b6f`.
- SSH Codex yes/no on `testbox`: `pre=7eb20c24`, `post=a06e2b6f`.
- Local Claude numbered choice: `pre=548ae2c9`, `post=12fd4959`.
- SSH Claude numbered choice on `testbox`: `pre=548ae2c9`, `post=12fd4959`.

Automation remains disabled and dry-run remains enabled in the shipped and
example configuration. Live acceptance used explicit disposable one-time
decisions, not automatic fleet policy.

## Acceptance Checklist

- [x] Durable harness-aware prompt records distinguish confirmed prompts from
      attention hints and coalesce, expire, or stale by fingerprint.
- [x] Typed scoped policy and conservative risk classification prevent
      non-routine Tier 0 execution and shell-prefix authority expansion.
- [x] Deterministic, fast-review, full-council, and human tiers produce durable
      provenance without giving controllers execution tools.
- [x] The action request binds prompt, worker, pane, fingerprint, and semantic
      choice; the arbiter recaptures and reparses before bounded input.
- [x] Post-action verification proves that the original prompt cleared rather
      than accepting arbitrary screen activity.
- [x] Dry run, global pause, automation enablement, host, project, session,
      worker, expiration, revocation, and rate controls are enforced.
- [x] Restart recovery cannot replay an action with an uncertain outcome.
- [x] Candidate policy learning does not silently grant authority.
- [x] The web approval center exposes prompt decisions, review progress, scoped
      controls, policy lifecycle, and auditable pre/post outcomes.
- [x] Automated backend, frontend, build, risk-corpus, and disposable live local
      and SSH acceptance all pass.
