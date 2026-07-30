# Agent Overlord

A local web control plane for observing and understanding agent sessions running
in tmux across local and SSH-accessible hosts. A persistent Python service owns
inventory, council, memory, and SQLite state; the React client receives live
updates without competing with chat input.

## Setup

```console
uv sync
npm --prefix web install
```

Copy `config.example.yaml` to `config.yaml`, or pass a tmux-watcher-compatible
configuration explicitly.

## Development

Honcho starts FastAPI and Vite from the root `Procfile`:

```console
uv run honcho start
```

Open <http://127.0.0.1:5173>. FastAPI listens on `127.0.0.1:8000`; Vite proxies
the API and event stream. Stopping or reloading the browser does not stop
inventory.

## Normal use

Build the frontend once, then run the single Python service:

```console
npm --prefix web run build
uv run agent-overlord serve
```

Open <http://127.0.0.1:8000>. Node is not a runtime process in this mode.

By default, Agent Overlord reads `config.yaml`. A different tmux-watcher-compatible
configuration can be supplied explicitly:

```console
uv run agent-overlord serve --config ./tmux-watcher/config.yaml
```

Existing tmux-watcher keys such as `quick_actions` are safely ignored. Pane input
is available only through the typed approval arbiter described below; arbitrary
configured key strings are never executed.

Operational state is stored in a platform-appropriate user data directory. Use
`--database PATH` to select a different SQLite database, or `--once` to perform a
single read-only inventory and print JSON without starting the service.

```console
uv run agent-overlord --config ./tmux-watcher/config.yaml --once
```

The former Textual client remains available as a diagnostic fallback with
`uv run agent-overlord tui`. New installations remain observation-only because
approval automation is disabled and dry-run mode is enabled by default.

## Prompt approval and safe pane input

The web header's **Approvals** view contains structured live prompts, their exact
operation and choices, risk, review tier, policies, automation posture, and action
history. Prompt decisions are bound to an exact observation fingerprint.

The control plane is the only component able to respond. Immediately before an
action it locks the pane, recaptures it through the configured local or SSH tmux
transport, reparses the prompt, and rejects any fingerprint, operation, or choice
change. It sends only the captured choice's bounded response and verifies that
the pane changed afterward. Controllers have tools to inspect and review prompt
records but no tmux, SSH, arbitrary key, or host-command tool.

Review tiers are:

- **Policy:** exact or structured argument-prefix match for routine operations.
- **Fast:** one configured low-latency controller, with a separate timeout.
- **Council:** operator, auditor, and strategist each submit a typed review.
- **Human:** an explicit choice in the approval center.

Elevated, high-risk, credential, unknown, or structurally unparsed operations do
not use deterministic policy. Prefix policies reject shell control operators in
the unmatched suffix. High-risk actions remain human-gated after review.

Safe defaults and the fast reviewer are configured with:

```yaml
fast_reviewer_controller_id: auditor
fast_review_timeout_secs: 60
automation:
  automation_enabled: false
  dry_run: true
  paused: false
  auto_yes_workers: []
  prompt_expiration_secs: 120
  verification_timeout_secs: 8
  max_actions_per_pane_per_hour: 20
  auto_yes_max_actions_per_worker_per_hour: 100
  review_precedent_ttl_secs: 604800
```

Settings are seeded into SQLite on first startup and then managed in the approval
center. Enable live input deliberately by turning off dry run. Automatic Tier 0
actions additionally require automation to be enabled and an active explicit
policy. A session row's **Auto yes** control is an explicit, pane-scoped grant
for recognized routine and elevated prompts in that worker pane; high-risk and
unclassified prompts still require review. Turning it on also enables live
automation and exits dry-run mode. Global pause and scoped disable lists stop
subsequent actions.

Verified fast/full-council approvals also act as exact review precedents for
seven days by default. Reuse requires the same normalized command, project,
host, harness, prompt type, risk, and semantic choice, and still performs live
recapture plus post-action verification. High-risk prompts never use precedents.

Useful APIs include:

- `GET /api/prompts`
- `POST /api/prompts/{prompt_id}/decision`
- `POST /api/prompts/{prompt_id}/review`
- `GET|POST /api/approval-policies`
- `PATCH /api/automation-settings`

After three matching verified one-time human decisions, Agent Overlord may create
a candidate exact policy. Candidate and suspended policies grant no authority
until explicitly activated.

## Inventory lifecycle

Open a worker from the sessions table to manage how it appears in inventory:

- **Permanently gone** removes a stale persisted worker after its pane has been
  observed as disconnected. The action is intentionally unavailable for a live
  worker; a still-running pane would otherwise be rediscovered immediately.
- **Ignore tmux session** persistently excludes every pane in the selected
  host/socket/tmux-session identity. This is appropriate for Agent Overlord's own
  control session or other sessions the council must never manage.

Ignored sessions appear in the web header, where **Restore excluded** clears all
persistent exclusions and immediately reconciles the inventory. They can also be
inspected with `GET /api/ignored-sessions` and individually restored with
`DELETE /api/ignored-sessions/{ignore_id}`. These inventory actions never
send commands or keystrokes to tmux.

## Persistent semantic council

When `controller_runtime_enabled: true`, council chat schedules durable semantic
work for three role-specific controllers: operator, auditor, and strategist. Each
controller runs in its own warm Podman container and resumes its native Claude
session or Codex thread between notification cycles. Human questions have highest
priority; background worker analysis is coalesced and throttled by
`worker_analysis_cooldown_secs`.

The normal topology remains one Python service. FastAPI embeds the scheduler and
starts a second, loopback-only MCP listener (port 8001 by default). Podman's
`pasta` network forwards only that MCP port into each controller container. The
main UI/API port is not reachable from controllers. Vite remains development-only.

### Build the controller image

```console
podman build \
  -t localhost/agent-overlord-controller:latest \
  -f container/controller/Dockerfile \
  container/controller
```

The image contains current Claude Code and Codex CLIs and runs as the unprivileged
`agent` user. Rebuild it when either harness needs upgrading.

### Configure controllers

```yaml
controllers:
  - controller_id: operator
    role: operator
    harness: claude.vertex
    model: sonnet
    environment:
      CLAUDE_CODE_USE_VERTEX: "1"
      CLOUD_ML_REGION: global
      ANTHROPIC_VERTEX_PROJECT_ID: your-gcp-project
  - controller_id: auditor
    role: auditor
    harness: codex
    # Uses the model supported by the authenticated Codex account.
    model: default
  - controller_id: strategist
    role: strategist
    harness: claude.vertex
    model: opus
    environment:
      CLAUDE_CODE_USE_VERTEX: "1"
      CLOUD_ML_REGION: global
      ANTHROPIC_VERTEX_PROJECT_ID: your-gcp-project

controller_runtime_enabled: true
controller_image: localhost/agent-overlord-controller:latest
controller_mcp_url: http://127.0.0.1:8001
controller_restart_limit: 3
notification_retry_limit: 2
worker_analysis_cooldown_secs: 900
```

`controller_mcp_url` must remain an explicit HTTP loopback URL. Agent Overlord
rejects non-loopback controller gateway configuration.

### Credentials

Claude Vertex controllers require Application Default Credentials at either
`$GOOGLE_APPLICATION_CREDENTIALS` or
`~/.config/gcloud/application_default_credentials.json`. Codex requires
`~/.codex/auth.json`. At container launch Agent Overlord copies only the relevant
credential into its controller-state directory with mode `0600`, then mounts that
copy read-only. It never mounts `.ssh`, the tmux socket, or a host workspace.

The role prompt and bearer-authenticated MCP configuration are also generated as
mode-`0600` files and mounted read-only. Rootless Podman uses a keep-id user
namespace so these files remain readable without weakening host permissions.

### Start and inspect

Development still uses Honcho:

```console
uv run honcho start
```

For normal use with the built React application:

```console
npm --prefix web run build
uv run agent-overlord serve --host 127.0.0.1 --port 8000
```

The service starts and supervises all configured controller containers. Useful
read-only endpoints include:

- `GET /api/controllers`
- `GET /api/council/notifications`
- `GET /api/workers/{worker_id}/interpretations`
- `GET /api/council/proposals`
- `GET /api/council/proposals/{proposal_id}`

Controller lifecycle, interpretations, messages, proposals, critiques, votes,
and notification outcomes also appear on the wall. The chat panel shows each
controller's state and pending, completed, failed, or timed-out work without
blocking another message.

### Logs and recovery

Controller logs and generated configuration live beside the SQLite database in
`controller-logs/` and `controllers/`. Logs include commands, structured harness
output, duration, exit status, session/thread IDs, and usage metadata; bearer
tokens are not written into command logs.

Nonzero exits and timeouts fail only the affected controller. A later turn
recreates that controller's warm container and starts a fresh native session;
durable interpretations, wall events, notifications, memories, proposals, and
votes remain authoritative in SQLite and are retrieved again through MCP.

The browser API never exposes controller tokens. The controller-facing MCP
gateway requires the exact per-controller bearer token, and its tool set contains
no pane-write, arbitrary command, repository mutation, SSH, or worker-lifecycle
operation.

## Test

```console
uv run pytest
npm --prefix web test
npm --prefix web run build
```
