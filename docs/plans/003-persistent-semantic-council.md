# Plan: Persistent Semantic Control Council

## Status

Implemented (2026-07-16)

## Purpose

Replace the deterministic council-chat facade with a persistent, mixed-model
control council that can semantically interpret worker activity, answer questions
using evidence, deliberate through the wall, and produce structured recommendations
without yet receiving authority to type into worker panes.

This phase adapts the proven persistent-agent patterns in `repos/cosim`: one warm,
isolated container per controller persona, resumable native Claude or Codex
sessions, an MCP-only interface to authoritative application state, event-driven
notification cycles, per-agent execution locks, explicit completion signaling,
and centralized lifecycle supervision.

## User Outcome

The user can ask questions such as:

- What is the goal of `observatory.1`?
- What is this worker doing now, and how does that relate to its overall goal?
- What exactly is it asking permission to do?
- Which workers are blocked, duplicated, stale, or working at cross-purposes?
- What evidence supports the council's interpretation?
- What action does the council recommend, and do its members agree?

The resulting answer should synthesize the relevant worker capture, history,
project context, ledger state, memories, and council deliberation. It should say
when evidence is insufficient rather than presenting a generic classifier label as
an explanation.

## Reference Architecture

The primary reference implementation is the checked-out `repos/cosim` project,
especially:

- `lib/agent_backends.py` for Claude/Codex invocation strategies and native
  session continuation.
- `lib/container_orchestrator.py` for warm containers, per-agent locks, process
  timeouts, logs, session recovery, and lifecycle management.
- `docs/agent-loop-architecture-v3.md` for notification-driven agents, MCP-only
  state access, completion signaling, isolation, and loop prevention.

Agent Overlord should adapt these patterns to its own domain rather than directly
coupling to cosim packages or treating cosim as a runtime dependency.

## Target Architecture

```text
Browser
├── council chat
├── council wall
└── agent sessions
        │
        ▼
FastAPI control plane + SQLite
├── authoritative worker inventory
├── observations and wall history
├── memories and controller state
├── council scheduler
├── proposal and vote records
└── deterministic action boundary
        │
        ▼
Agent Overlord MCP service
├── read workers and captures
├── read relevant wall history
├── read memories and project context
├── post interpretations and deliberation
├── submit proposals, critiques, and votes
└── signal notification-cycle completion
        │
        ├──────────────┬──────────────┐
        ▼              ▼              ▼
Claude operator   Codex auditor   Claude/Codex strategist
warm container    warm container  warm container
resumed session   resumed thread  resumed session/thread
```

The FastAPI service and SQLite database remain authoritative. Controller harness
conversation history is useful working context but is not the source of truth for
workers, decisions, memories, authority, or action outcomes.

## Controller Roles

Begin with three differentiated controllers mixing Claude and Codex.

### Operator

- Maintains the active interpretation of workers and intents.
- Investigates changed, uncertain, blocked, or user-selected workers.
- Produces an evidence-backed synthesis for routine council questions.
- Proposes actions that might advance work, without executing them.

### Auditor

- Checks claims against current observations and durable records.
- Identifies stale evidence, unsafe assumptions, scope expansion, and missing
  verification.
- Critiques action proposals and completion claims.
- May veto a recommendation when evidence or authority is insufficient.

### Strategist

- Considers relationships across workers, projects, and user intents.
- Detects duplication, dependency conflicts, resource imbalance, and abandoned
  work.
- Suggests whether to continue, redirect, consolidate, or eventually create work.
- Helps synthesize disagreements between the operator and auditor.

Role-to-harness and model assignments should be configuration rather than embedded
in application logic. The initial configuration should include at least one
`claude.vertex` controller and at least one Codex controller.

## Persistent Agent Runtime

Each controller should have a stable logical identity and a supervised warm
Podman container.

The container manager should:

- Start one container per configured controller.
- Generate and mount role instructions and MCP configuration read-only.
- Stage only the credentials required by that controller's harness.
- Retain the container between notification cycles.
- Invoke a discrete harness turn with `podman exec` when work is queued.
- Continue Claude sessions by session UUID and Codex sessions by thread ID.
- Serialize turns through one asyncio lock per controller.
- Capture stdout, stderr, duration, exit status, token metadata where available,
  and the native session identifier.
- Enforce turn and completion timeouts.
- Reset a corrupt native session without discarding authoritative council state.
- Restart failed containers with bounded backoff.
- Stop and remove containers cleanly with the control-plane lifecycle.

Warm containers and resumable sessions provide controller continuity without
requiring one permanently active model call. Controllers consume model capacity
only when a notification cycle invokes them.

## Harness Backend Boundary

Introduce an Agent Overlord backend protocol modeled on cosim's strategy pattern.
It should cover:

- Harness command construction.
- Initial session creation and later resumption.
- Per-harness configuration generation.
- Credential staging and read-only mounts.
- Structured event/output parsing.
- Session and usage metadata extraction.
- Error classification and recoverability.

Initial adapters:

- `ClaudeControllerBackend`, supporting the local `claude.vertex` environment and
  native Claude session UUIDs.
- `CodexControllerBackend`, supporting `codex exec` and resumable thread IDs.

Agent Overlord should reuse ideas and tests from cosim, but security flags must be
re-evaluated for this product. In particular, cosim's broad Codex execution flags
must not be copied unless the container and MCP authority make them safe and the
need is documented.

## MCP Tool Surface

Controllers should interact with Agent Overlord through authenticated,
persona-scoped MCP tools. The initial surface should be read-mostly and
task-oriented.

Candidate observation tools:

- `list_workers(filters)`
- `get_worker(worker_id)`
- `get_worker_capture(worker_id, lines, before_version)`
- `get_worker_history(worker_id, limit)`
- `get_host_health(host)`
- `search_wall(query, filters, limit)`
- `get_chat_context(limit)`
- `search_memories(query, scopes)`
- `get_project_context(worker_id)`
- `get_ledger_context(worker_id)`

Candidate council tools:

- `post_wall_message(scope, message, references)`
- `record_interpretation(worker_id, interpretation, evidence, confidence)`
- `submit_proposal(operation, target, observation_version, rationale, risk)`
- `critique_proposal(proposal_id, findings)`
- `vote_on_proposal(proposal_id, vote, rationale)`
- `answer_human_message(message_id, answer, references)`
- `signal_done(notification_id, summary)`

No controller-facing MCP tool in this phase may send keys, execute arbitrary host
commands, mutate observed repositories, create workers, or expand its own
authority.

## Semantic Worker Interpretation

Extend the worker model beyond the current generic `purpose` string. Semantic
interpretations should distinguish:

- Overall goal or intended outcome.
- Current activity or immediate step.
- Current blocker, question, or requested decision.
- Requested command or operation when awaiting permission.
- Project, repository, branch, ledger task, and higher-level intent when known.
- Completion or success criteria when discoverable.
- Evidence references and observation content fingerprints.
- Confidence, freshness, interpreting controller, and interpretation version.

Deterministic classification remains the inexpensive first pass. A semantic
controller cycle should be scheduled when:

- A human asks a question requiring meaning rather than status.
- A worker is newly discovered or its capture changes materially.
- The deterministic purpose remains generic or confidence is low.
- A worker requests input, fails, stalls, or claims completion.
- Interpretations conflict or become stale.

The system should retrieve deeper scrollback, project metadata, and ledger context
only when necessary. Controller prompts should be notifications that encourage
targeted MCP retrieval rather than embedding the entire fleet state in every turn.

## Notification and Scheduling Model

The council scheduler should use durable notification records with stable IDs,
targets, reasons, priority, creation time, and completion state.

Initial notification sources:

- Human council-chat messages.
- Worker discovery and material output changes.
- Input requests, failures, stalls, and possible completion.
- New proposals or critiques requiring another role.
- Memory contradictions or stale interpretations.

Scheduling requirements:

- Human messages have highest priority.
- A controller processes at most one notification cycle at a time.
- Duplicate worker-change notifications may coalesce by observation version.
- Newer human direction may supersede queued lower-priority analysis.
- Each cycle has a bounded runtime and must end with `signal_done()` or timeout.
- Failures retry with limits and become visible on the wall.
- Agent-authored wall chatter must not create an unbounded reaction loop.

The first implementation may use role-aware waves: operator analysis, auditor
review, then strategist synthesis where needed. It should avoid requiring all
three agents for inexpensive factual questions when one answer plus deterministic
evidence is sufficient.

## Structured Wall and Council Records

Extend wall events and persistence for:

- Controller notification started/completed/failed.
- Semantic interpretation proposed or replaced.
- Action proposal submitted.
- Critique, vote, veto, and consensus result.
- Human question assigned and answered.
- Controller session reset or container restart.
- Usage, latency, and timeout summaries.

Consequential records should have typed database representations rather than
existing only as prose. Wall rendering may remain compact while expandable detail
shows exact evidence, controller identities, observation versions, and votes.

## Chat Behavior

Council chat should route semantic questions through the persistent council rather
than the deterministic phrase matcher.

The UI should show:

- Which controllers are investigating or reviewing the question.
- A pending state that does not block typing another message.
- A synthesized answer with links to workers and wall evidence.
- Disagreement or uncertainty when consensus is absent.
- Failure and timeout states with an option to retry.

Simple deterministic queries may still return immediately, but the system must not
use a generic status response when the user asked for semantic explanation.

## Memory Integration

Controllers may retrieve existing scoped memories. New inferred memories should
begin as candidates with provenance rather than becoming active silently.

This phase should record:

- Which memories influenced an interpretation or recommendation.
- Candidate semantic or procedural memories proposed by controllers.
- Human corrections as high-priority contradictory evidence.
- Native controller session resets independently from durable shared memory.

Automatic reinforcement, retirement, and broad procedural learning remain limited
until council output quality is observable.

## Safety and Isolation

- Controller containers receive no host tmux socket.
- Controller containers receive no SSH credentials for observed hosts.
- Controller containers receive no general host-workspace mount.
- MCP is the only supported interface to Agent Overlord state.
- Tools are authorized per controller identity on the server, not merely hidden by
  prompt instructions.
- Every interpretation and proposal names the observation version or content
  fingerprint it used.
- The deterministic Python service owns all lifecycle and future side effects.
- This phase does not expose pane-write authority, even when all controllers vote
  for an action.
- Credential mounts are minimal, read-only where harness behavior permits, and
  excluded from logs.

## Process Topology

The implementation keeps controller scheduling and MCP supervision in the normal
Python service. FastAPI starts a separate loopback-only MCP listener, while
Podman `pasta` forwards only that listener's port into controller containers:

```procfile
api: uv run agent-overlord serve --host 127.0.0.1 --port 8000
web: npm --prefix web run dev
```

Normal use remains operable without Vite. The dedicated MCP listener defaults to
`127.0.0.1:8001`; it is not mounted into the browser-facing app, and controller
containers cannot reach the main API port.

## Implementation Sequence

1. Stabilize remote inventory reconciliation so transient partial discovery does
   not flood the wall or provide false evidence to controllers.
2. Define typed semantic interpretations, notifications, proposals, critiques,
   votes, and controller-runtime state.
3. Define the MCP authority model and implement read-only observation tools.
4. Extract and adapt cosim's backend strategy for Claude Vertex and Codex.
5. Implement the supervised warm-container pool, locks, logging, timeouts, session
   continuation, and recovery.
6. Add the three controller personas and role-specific system instructions.
7. Implement durable notification scheduling and `signal_done()` handling.
8. Add semantic analysis for user-selected workers and council questions.
9. Add proposal, critique, and voting tools without execution authority.
10. Route council chat through controller cycles and show progress in the web UI.
11. Add controller health, usage, failure, and session-reset visibility.
12. Exercise the system against live local and remote workers and tune triggering,
    retrieval, and loop prevention.

These steps describe sequencing within the plan. They are not yet ledger task
assignments.

## Non-Goals

- Sending keys or commands to worker panes.
- Automatically approving permission prompts.
- Launching, terminating, or redirecting workers.
- Intent decomposition and autonomous worker creation.
- Giving controllers arbitrary shell or host filesystem access.
- Treating native Claude/Codex conversation history as durable system state.
- Requiring unanimous three-agent deliberation for every factual query.
- Public or non-loopback exposure of the control plane or MCP service.
- Directly importing cosim as an application dependency.

## Acceptance Criteria

- [x] Three configured controller personas run in separately supervised warm
      containers, including at least one Claude Vertex and one Codex controller.
- [x] Each controller retains and resumes its native harness session across
      notification cycles.
- [x] Per-controller locking prevents overlapping turns and timeout recovery does
      not corrupt other controllers.
- [x] Controllers can be restarted with a fresh native session while recovering
      authoritative state through MCP.
- [x] Controller containers have no tmux socket, observed-host SSH credentials, or
      general host-workspace access.
- [x] Server-side MCP authorization limits every controller to the documented tool
      surface.
- [x] The semantic worker record distinguishes overall goal, current activity,
      blocker/request, evidence, confidence, freshness, and observation version.
- [x] Asking “what is the goal of `<worker>`?” produces an evidence-backed semantic
      answer or an explicit statement that available evidence is insufficient.
- [x] Human chat messages schedule high-priority controller work and the UI shows
      pending, completed, failed, and timed-out states.
- [x] Controllers retrieve targeted current context through MCP rather than
      receiving an unconditional full-fleet prompt dump.
- [x] Interpretations, proposals, critiques, votes, and controller lifecycle events
      are durably persisted and visible on the wall.
- [x] The operator, auditor, and strategist can disagree without losing their
      individual evidence or rationale.
- [x] No controller tool or API route can send input to a worker pane in this
      phase.
- [x] Agent-authored events cannot trigger an unbounded council loop.
- [x] Remote discovery flapping is controlled sufficiently that controller cycles
      are not repeatedly triggered by partial snapshots.
- [x] Claude/Codex backend, container lifecycle, session continuation, scheduler,
      MCP authorization, failure recovery, and chat interaction tests pass.
- [x] Controller startup, shutdown, configuration, logs, and credential
      requirements are documented.

## Implementation Evidence

The 2026-07-16 acceptance run used the real local and SSH inventory and the built
`localhost/agent-overlord-controller:latest` image.

- Podman started three distinct warm containers: Claude Vertex operator, Codex
  auditor, and Claude Vertex strategist. Inspection showed only role prompt/MCP
  config and harness-specific credential mounts, all read-only; no tmux socket,
  SSH directory, or workspace was mounted.
- The main API remained bound to `127.0.0.1:8000`. Each container received only a
  `pasta` TCP forward to the separate `127.0.0.1:8001` MCP gateway. A controller
  could not reach port 8000, and an unauthenticated gateway request returned 401.
- A live question about `observatory.2` produced a versioned interpretation at
  confidence 0.95 and an answer citing capture fingerprint
  `96e855ce130142ea177b0a9a39acb7dfd312f6716027feefc20453d1cf485347`.
- Claude resumed one native session over multiple notification cycles. A focused
  Codex acceptance run completed two authenticated MCP cycles with
  `signal_done`; both retained thread ID
  `019f6cbb-fa33-7921-a8dd-a51f90b20ac3` and the second correctly referenced the
  first cycle's result.
- Live defects found during the run were fixed before acceptance: rootless bind
  mount UID mapping, separation of UI/API and MCP listeners, Codex model
  selection and resume syntax, MCP-specific tool approval, mandatory completion
  signaling, and background analysis throttling.
- Final automated verification is recorded by the repository's Python, React,
  and production-build commands documented in the README.

## Implementation Decisions

- MCP gateway supervision and council scheduling are embedded in the Python
  service; the gateway has its own loopback listener and restricted container
  port forward.
- Role, harness, and model are configuration. The initial mapping is Claude
  operator, Codex auditor using its account-supported default model, and Claude
  strategist.
- Human semantic questions use the three role wave when those roles are enabled.
  Background worker changes initially target the operator and are throttled.
- Controllers use the global wall with worker IDs and typed references rather
  than separate channel infrastructure.
- State transitions schedule immediately. Same-state content changes are
  coalesced by fingerprint and limited by `worker_analysis_cooldown_secs`.
- Native sessions persist until harness failure or container restart. Deliberate
  token/cycle rotation remains a tuning follow-up.
- Cosim's backend strategy, warm-container, lock, and resumable-session patterns
  were adapted to Agent Overlord's asyncio lifecycle; cosim is not imported.
- Per-controller bearer tokens are generated on service start and written only to
  mode-`0600` controller configuration. Restarting the service rotates them.
- Semantic interpretations are immutable, versioned records keyed to an exact
  observation fingerprint.

## Deferred Follow-On

Once this observe-and-recommend council is demonstrably reliable, a separate plan
should define the deterministic action arbiter, risk-based authority envelopes,
prompt recapture, consensus requirements, pane input, and post-action
verification. Intent-driven worker creation should remain a later plan after the
action boundary is proven.

## Related Artifacts

- [Exploratory control-plane notes](../notes/session-log.md)
- [Observation-first MVP](001-mvp.md)
- [Local web control plane](002-local-web-interface.md)
- [ADR-0002](../decisions/ADR-0002-local-web-control-plane.md)
- `repos/cosim/lib/agent_backends.py`
- `repos/cosim/lib/container_orchestrator.py`
- `repos/cosim/docs/agent-loop-architecture-v3.md`
