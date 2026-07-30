# Session Notes

## 2026-07-16 — Exploratory notes on a tmux-based agent control plane

Status: Discussion notes only. These observations are not an implementation plan,
accepted architecture, or committed set of tasks.

### Motivation

There are many tmux sessions, windows, and panes spread across the local machine
and SSH-accessible machines. Many panes contain Claude or Codex agents doing
ongoing work. Those agents frequently stop at permission questions that currently
require a human to inspect the pane and answer prompts such as yes/no requests.

`tmux-watcher` is useful as a reference and as a manual interface. It inventories
local and remote tmux panes, captures their output, recognizes attention patterns,
and sends keys to selected panes. The system discussed here would be a separate
project with a different purpose and presentation layer: an always-running agent
control plane that keeps work moving with the least human input necessary.

### Existing environment and useful prior art

- Every target tmux host is reachable from localhost, either locally or over SSH.
- `tmux-watcher/config.yaml` already provides an initial registry of those hosts.
- The useful `tmux-watcher` techniques include structured `tmux list-panes`
  formats, `capture-pane`, `send-keys`, persistent SSH connections, and composite
  host/pane identities.
- Goosetown, OpenClaw, Hermes, and similar agent runtimes are relevant sources of
  patterns for persistent loops, tool use, memory, delegation, and coordination.
- Goosetown's `gtwall` is particularly relevant. Its append-only communication,
  per-reader cursors, session isolation, concurrent writers, and prioritization of
  human messages provide a useful coordination model.
- The Agent Work Ledger can provide durable project-level task and decision state.
  Operational fleet state will probably need a separate local store.

### Emerging product concept

The tentative mental model is an autonomous supervisory control plane rather than
a smarter pane dashboard. Tmux is the worker runtime and interaction substrate,
not the source of truth or the whole application.

The system would repeatedly:

1. Observe sessions, windows, panes, processes, repositories, and ledger state.
2. Maintain a semantic inventory of what each pane is for and what it is doing.
3. Detect progress, completion, failure, stalls, questions, and permission prompts.
4. Reconcile observed state against higher-level user intents.
5. Propose and evaluate actions that could advance the work.
6. Perform actions permitted by policy.
7. Verify their effects rather than assuming that sent keys succeeded.
8. Record events and continue until the intent is complete, paused, or genuinely
   requires the human.

The desired interaction style is closer to an always-on OpenClaw/Hermes-like
runtime than a CLI command that produces a one-time inventory.

### Semantic inventory

A pane record may eventually include:

- Host, tmux server/socket, session, window, and pane identifiers.
- Working directory, repository, branch, and relevant git state.
- Foreground command and likely harness or agent type.
- A semantic statement of the pane's purpose.
- Current state such as active, idle, blocked on permission, failed, stalled, or
  complete.
- The action currently being attempted or requested.
- Confidence, freshness, and evidence for the interpretation.
- A stable logical worker identity above tmux's short-lived pane IDs.
- Ownership state such as human-owned, controller-owned, shared, or observe-only.

Deterministic detection should handle common and inexpensive cases. Semantic
agent analysis should be invoked when output changes materially or when the
deterministic interpretation is insufficient.

### Multiple control agents

More than one high-level control agent is desired. A three-member council mixing
Claude and Codex is appealing because it allows proposals, critique, and consensus
across different models and harnesses.

Possible differentiated roles discussed:

- An operator that maintains the active interpretation and proposes routine
  actions.
- An auditor that checks evidence, policy, scope, and completion claims.
- A strategist that considers cross-worker allocation, duplication, stalls, and
  alternative approaches.

The agents should communicate through a wall-like channel. Free-form messages are
useful for discoveries and discussion, while consequential proposals, votes, and
action results should also have structured fields.

Only one deterministic action arbiter should own side effects. Control agents
should not independently type into the same panes. The arbiter would validate
consensus, apply policy, reject stale proposals, execute an action once, verify its
effect, and publish the result.

### Wall and consensus ideas

Potential communication scopes include a global fleet wall, one wall per intent,
and narrower worker channels. Human wall messages should have priority.

Consensus requirements should vary with risk rather than applying a universal
two-of-three rule:

- Observation and other read-only operations may require no vote.
- Routine actions already covered by policy may allow one proposal with a short
  veto window.
- Changes of direction and new worker creation may require two explicit votes.
- Completion should require review, including the auditor.
- Publishing, destructive actions, or operations outside the authority envelope
  may require unanimity or explicit human approval.

Every actionable proposal should name the exact interpreted operation and the
observation version or content fingerprint on which it is based. Before sending a
response, the arbiter should recapture the pane and ensure that the prompt has not
changed. Consensus to type a generic `yes` is not sufficient.

The original `gtwall` filesystem interface is attractive, but a shared file alone
does not cover multiple machines. An `agentwall`-style CLI could preserve the
simple agent experience while talking to a localhost daemon and durable event
store. Initially, all control agents could run on localhost and the supervisor
could relay work to remote panes, avoiding the need to expose the wall directly to
remote workers.

### Authority and minimum human involvement

Autonomy should be governed by a durable authority envelope associated with an
intent. It could define allowed hosts, repositories, actions, worker count, time or
retry budgets, and operations that require escalation.

The supervisor should normally act without interruption for operations that are
clearly inside that envelope. Human input should be reserved for meaningful
ambiguity, expansion of authority, external side effects, credential boundaries,
high-risk operations, repeated failure, and final acceptance where appropriate.

Permission handling must evaluate the operation being approved, not merely detect
a yes/no prompt. Running an in-scope test may be automatically permitted, while a
destructive command or an unexplained network/install operation may be escalated.

Possible operating modes include observe-only, recommend, and act. These modes
could be set globally, per intent, per host, or per worker.

### Intent-driven worker creation

The eventual system should accept a high-level intent and decide whether to resume
an existing relevant worker or create a new one. Creating a worker may involve:

- Resolving the appropriate project, host, repository, and worktree.
- Choosing a harness. The current preference is generally `claude.vertex` for
  work projects and Codex for personal projects.
- Creating a tmux session/window and applying a named version of the user's usual
  pane-splitting layout.
- Installing or resuming the Agent Work Ledger instructions.
- Giving the worker a scoped task, authority, and communication identity.
- Registering it with the semantic inventory and control council.

The ledger instructions should likely come from a pinned or versioned local copy
rather than being downloaded without verification for every launch.

### Durable state

The discussion distinguished several kinds of state:

- Tmux holds live processes and provides the interaction surface.
- A local operational database may hold workers, observations, intents, approvals,
  proposed actions, votes, and an append-only event history.
- Ledger files hold durable project tasks, discoveries, decisions, acceptance
  criteria, and evidence in a form suitable for git history.
- Git records project changes but should not be treated as the live message bus.

Recovery after a daemon or controller restart should rediscover tmux state and
reconcile it with durable logical worker identities. No action should be marked
successful merely because input was sent.

### Open questions and areas to explore

- How should stable worker identity survive tmux restarts, renamed sessions, and
  changed pane IDs?
- Which exact commands and side effects belong in the initial automatic approval
  policy?
- What metadata is reliably available from the existing sessions and harnesses?
- How should work versus personal classification be determined when it is not
  explicit in host or repository configuration?
- What is the best balance between deterministic classifiers and semantic model
  calls?
- How should controller leadership, timeouts, vetoes, degraded quorum, and model
  failure be handled without stalling the loop?
- Should controllers edit ledgers directly, or coordinate ledger changes through
  project workers?
- What transport should back the wall across process and machine boundaries while
  retaining the simplicity of `gtwall`?
- Which concepts from Goosetown, OpenClaw, and Hermes transfer cleanly, and which
  depend on assumptions that do not hold for independently created tmux sessions?

### Current direction, without commitment

A plausible early experiment is three persistent, mixed Claude/Codex control
agents on localhost sharing a structured wall. They would observe all hosts from
the existing tmux-watcher registry, classify existing worker panes, deliberate
about permission prompts, and pass approved actions to a single arbiter. Beginning
with narrow authority would exercise the complete observe-understand-decide-act-
verify loop before attempting broad task planning or automatic worker creation.

## 2026-07-16 — Interface and control-agent memory notes

Status: Continued exploratory notes. These are interface and memory concepts, not
an accepted design or implementation plan.

### Initial interface concept

An initial interface could have three vertically arranged panels:

1. A table of agent-bearing tmux panes that summarizes the current fleet.
2. A live, tailing stream of wall activity.
3. A chat interface for querying and instructing the control council.

The table is intended to be the initial MVP and a source of feedback for later
ideas. A row should internally represent an agent-bearing pane, even if the UI
calls it an agent session, because a tmux window may contain multiple agents.

Candidate columns include:

- Host.
- Human-readable tmux identity, such as `session:window.pane`.
- Semantic purpose of the agent or pane.
- Harness, such as Codex, Claude Vertex, another Claude CLI, or shell.
- Model, when detectable or declared.
- Context size, usage, remaining capacity, or an unknown state.
- Current state, including whether the agent is awaiting input.

The initial `awaiting-input` value may later become a richer state vocabulary such
as active, thinking, awaiting permission, awaiting clarification, stalled, failed,
complete, idle, disconnected, or unknown. Requests for input may also be divided
into permission, clarification, selection, credentials, completion confirmation,
and unclassified prompts.

The semantic purpose may be declared or inferred. The interface should expose the
confidence and evidence behind inferred purposes rather than presenting uncertain
interpretations as facts. Row expansion could show recent pane output, working
directory, repository, branch, process, connection status, interpreted request,
risk, policy result, and available actions.

### Live wall panel

The middle panel could tail the shared control-agent wall. It may be busy, but the
visible activity helps the user understand that the autonomous system is alive,
what it is noticing, and why state changes occur.

The wall should be a structured event stream rendered like a compact log. Useful
visible events include:

- Worker discovery and changed semantic purpose.
- State transitions such as blocked, resumed, stalled, failed, or complete.
- Permission requests.
- Control-agent proposals, critiques, votes, vetoes, and consensus.
- Arbiter execution and verification results.
- Intent changes and human instructions.
- Host disconnection and recovery.
- Important memory creation, reinforcement, contradiction, or replacement.

Routine polling and unchanged pane captures should not appear. The wall should
show meaningful transitions rather than every observation operation.

Useful controls may include follow, pause scrolling, jump to latest, filtering by
agent, host, intent, worker, event type, or severity, and text search. Pausing the
view should not pause the control system. Compact events should be expandable to
show the exact proposal, observation version, votes, applicable policy, execution,
and verification evidence.

The three panels should cross-reference one another. Selecting a table row could
filter the wall; selecting a wall event could highlight a worker; and council chat
answers could link to the relevant workers and events.

The wall should remain the faithful activity record. The chat should provide a
synthesized explanation of what the activity means and accept natural-language
queries and instructions from the user. Detailed council deliberation can be
available on demand without dominating the chat.

### Durable control-agent memory

The control agents should retain durable memories about what they learn. The wall
provides short-term shared awareness, while memory should preserve useful
knowledge across controller restarts, context loss, and separate intents.

Several kinds of memory were identified:

- Episodic memory: specific past events, actions, failures, and outcomes.
- Semantic memory: stable facts about hosts, repositories, harnesses, and the
  environment.
- Procedural memory: successful ways to recognize states, handle prompts, run
  workflows, or recover from recurring failures.
- Preference memory: user choices about routing, autonomy, layouts, approval,
  notification, and work style.

Memory should be explicitly scoped. Potential scopes include global/user, host,
project, intent, and worker. Project-specific memory should not silently influence
unrelated projects. Explicit instructions in `AGENTS.md`, an intent, or the ledger
should outrank learned assumptions.

Control agents may maintain temporary private working memory, but useful learning
should be proposed for promotion into shared memory so that Claude and Codex
controllers do not develop isolated and inconsistent views. A memory curator role
or deterministic service could validate, deduplicate, merge, and retire memories.

Durable memories should include provenance and lifecycle information where
appropriate:

- Stable memory identifier.
- Scope and kind.
- Claim or learned procedure.
- Source observations, events, instructions, or wall messages.
- Creating agent.
- Creation and last-confirmation times.
- Confidence and number of confirmations.
- Status such as candidate, active, reinforced, contradicted, superseded, or stale.

Memories must remain correctable. Repeated confirmation may increase confidence;
new instructions or contradictory evidence may supersede a memory; and unused or
unverifiable environment facts may become stale. A user instruction to forget or
replace a preference should take immediate precedence.

Action/outcome pairs are especially valuable procedural memories. The system may
learn that a particular interpretation and approval reliably resumes a given
harness, or that a particular recovery technique repeatedly fails. Human
corrections should be recorded as strong negative feedback.

Important memory lifecycle events can appear on the wall, but routine memory
retrieval should not create noise. When a memory influences an autonomous action,
the system should be able to explain which memory it used and where that memory
came from.

The chat interface may support natural requests such as asking what the council
has learned about a repository, explaining why a memory influenced an action,
remembering a new preference, correcting an assumption, or forgetting an obsolete
procedure. Expanded worker rows may show the most relevant memories.

Operational memory may be stored in SQLite for retrieval, provenance, confidence,
and relationships. Curated long-lived knowledge may also be rendered into
human-readable Markdown grouped by user preferences, hosts, harnesses, and
projects. Project ledgers and `AGENTS.md` remain authoritative for project-specific
instructions and decisions; control-plane memory should reference rather than
silently replace them.

The guiding constraint is that control agents may learn continuously, but durable
memory must remain scoped, attributable, visible, and correctable so that stale
assumptions do not invisibly drive autonomous actions.

## 2026-07-16 — Observation-first MVP implementation

Agent: Codex implementation agent

Completed:

- Created the uv-managed Python 3.12+ package and committed lockfile.
- Implemented compatible local and system-SSH tmux discovery.
- Implemented typed observations, semantic worker classification, stable logical
  identities, state transitions, and disconnected-pane handling.
- Added SQLite persistence for workers, events, chat, shared memories, and
  independent wall-reader positions.
- Implemented the Textual sessions table, tailing/filterable wall, council chat,
  and worker evidence/output inspector.
- Added evidence-backed council status and inspection queries plus explicit memory
  teaching, retrieval, correction, and removal.
- Preserved the observation-first boundary; the application has no automatic
  pane-input transport.

Verified:

- `uv sync --locked` succeeds.
- All automated tests pass.
- All source and test modules compile.
- A live read-only inventory using the tmux-watcher host registry reached the
  configured hosts without errors and found both Claude and Codex sessions.

Discovered:

- A control-character tmux field delimiter was not portable across every remote
  host. The implementation now uses tab-separated tmux format output, matching the
  proven tmux-watcher approach.
- Thread-offloaded SQLite operations did not shut down reliably in the constrained
  execution environment. MVP transactions are intentionally small and serialized
  on the application loop behind an interface that can later move to a dedicated
  database worker.

Next:

- Persistent mixed Claude/Codex control-agent processes and an action arbiter can
  be designed on top of the existing council, wall, and application-service
  boundaries.
