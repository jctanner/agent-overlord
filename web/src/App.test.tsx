import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from './App'
import type { Memory, PromptRequest, Snapshot, Worker } from './types'

class FakeEventSource {
  static instance: FakeEventSource
  onerror: (() => void) | null = null
  listeners = new Map<string, Array<(event: MessageEvent) => void>>()

  constructor(_url: string) { FakeEventSource.instance = this }
  addEventListener(name: string, listener: EventListener) {
    const listeners = this.listeners.get(name) ?? []
    listeners.push(listener as (event: MessageEvent) => void)
    this.listeners.set(name, listeners)
  }
  emit(name: string, value: unknown) {
    for (const listener of this.listeners.get(name) ?? []) {
      listener(new MessageEvent(name, { data: JSON.stringify(value) }))
    }
  }
  close() {}
}

const now = '2026-07-16T12:00:00Z'
function worker(id = 'worker-one', purpose = 'Build the control plane'): Worker {
  return {
    worker_id: id,
    observation: {
      host: 'laptop', tmux_socket: 'default', session_id: '$1', session_name: 'overlord',
      window_id: '@1', window_name: 'agent', pane_id: `%${id}`, pane_index: 0,
      pane_title: 'codex', current_path: '/work', current_command: 'codex', start_command: 'codex',
      descendant_commands: [],
      content: ['Working carefully'], observed_at: now, display_name: `overlord:${id}.0`, content_fingerprint: id,
    },
    harness: 'codex', model: 'gpt-5', context: '80%', purpose, project: 'agent-overlord',
    state: 'active', awaiting_input: false, input_kind: null, confidence: 0.9, evidence: ['codex'],
    first_seen_at: now, last_seen_at: now, unchanged_since: now,
  }
}

const snapshot: Snapshot = {
  workers: [worker()], events: [], messages: [], memories: [],
  controllers: [], notifications: [], ignored_sessions: [], prompts: [], policies: [],
  automation: {
    automation_enabled: false, dry_run: true, paused: false, disabled_hosts: [],
    disabled_projects: [], disabled_sessions: [], disabled_workers: [],
    auto_yes_workers: [],
    prompt_expiration_secs: 120, verification_timeout_secs: 8,
    max_actions_per_pane_per_hour: 20, updated_at: now,
    auto_yes_max_actions_per_worker_per_hour: 100,
    review_precedent_ttl_secs: 604800,
  },
  health: {
    status: 'ok', inventory_running: true, started_at: now, configured_hosts: 1,
    workers: 1, stream_clients: 1, hosts: [{ name: 'laptop', connected: true, error: null, worker_count: 1 }],
  },
}

function prompt(): PromptRequest {
  return {
    prompt_id: 'prompt-one', worker_id: 'worker-one', host: 'laptop', tmux_socket: 'default',
    session_id: '$1', session_name: 'overlord', window_id: '@1', window_name: 'agent',
    pane_id: '%1', pane_index: 0, harness: 'codex', project: 'agent-overlord',
    prompt_type: 'permission', operation: 'uv run pytest -q',
    normalized_argv: ['uv', 'run', 'pytest', '-q'],
    choices: [{ choice: 'allow', label: 'Yes', response: 'y' }, { choice: 'deny', label: 'No', response: 'n' }],
    observation_fingerprint: 'a'.repeat(64), prompt_signature: 'b'.repeat(64),
    evidence: ['Allow command? (y/n)'], confidence: .95, risk: 'routine',
    risk_reasons: ['routine development command'], tier: 'human', status: 'detected',
    decision: null, selected_choice: null, decision_source: null, policy_id: null,
    reviewer_ids: [], rationale: null, error: null, created_at: now, updated_at: now,
    completed_at: null,
  }
}

function candidateMemory(): Memory {
  return {
    memory_id: 'memory-candidate', scope: 'global', kind: 'preference',
    claim: 'Do not switch models at an agent request', source: 'controller inference',
    created_by: 'strategist', confidence: 1, status: 'candidate',
    created_at: now, updated_at: now,
  }
}

beforeEach(() => {
  vi.stubGlobal('EventSource', FakeEventSource)
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/snapshot') return new Response(JSON.stringify(snapshot), { status: 200 })
    if (url === '/api/chat') return new Response(JSON.stringify({ message: 'ok', worker_ids: [] }), { status: 200 })
    return new Response('{}', { status: 200 })
  }))
})

test('excluded sessions can be restored and reconciled without a manual API call', async () => {
  const user = userEvent.setup()
  const excludedSnapshot: Snapshot = {
    ...snapshot,
    workers: [],
    ignored_sessions: [{
      ignore_id: 'ignore-one', host: 'laptop', tmux_socket: 'default',
      session_id: '$0', session_name: '0', created_at: now,
    }],
  }
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/snapshot') return new Response(JSON.stringify(excludedSnapshot), { status: 200 })
    if (url === '/api/ignored-sessions/restore-all') {
      return new Response(JSON.stringify({ restored_ignore_ids: ['ignore-one'], workers: [worker()] }), { status: 200 })
    }
    return new Response('{}', { status: 200 })
  })

  render(<App />)
  await user.click(await screen.findByRole('button', { name: /Restore excluded 1/ }))

  await waitFor(() => expect(screen.getByText('Build the control plane')).toBeInTheDocument())
  expect(screen.queryByRole('button', { name: /Restore excluded/ })).not.toBeInTheDocument()
  expect(fetch).toHaveBeenCalledWith('/api/ignored-sessions/restore-all', expect.objectContaining({ method: 'POST' }))
})

test('approval center exposes dry-run prompt decisions and scoped controls', async () => {
  const user = userEvent.setup()
  const pending = prompt()
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/snapshot') {
      return new Response(JSON.stringify({ ...snapshot, prompts: [pending] }), { status: 200 })
    }
    if (url === '/api/prompts/prompt-one/decision') {
      return new Response(JSON.stringify({ ...pending, status: 'decided', decision: 'allow', selected_choice: 'allow' }), { status: 200 })
    }
    return new Response('{}', { status: 200 })
  })

  render(<App />)
  await user.click(await screen.findByRole('button', { name: /Approvals 1/ }))
  expect(screen.getByRole('dialog', { name: 'Prompt approvals' })).toHaveTextContent('uv run pytest -q')
  expect(screen.getByText('No pane input will be sent')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Disable host' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Yes' }))
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(
    '/api/prompts/prompt-one/decision',
    expect.objectContaining({ method: 'POST', body: expect.stringContaining('"execute":false') }),
  ))
})

test('approval center exposes a readable council decision audit trail', async () => {
  const user = userEvent.setup()
  const reviewed: PromptRequest = {
    ...prompt(), status: 'succeeded', tier: 'council', decision: 'allow',
    selected_choice: 'allow', decision_source: 'council',
    reviewer_ids: ['operator', 'auditor', 'strategist'],
    review_notification_id: 'notification-one',
    review_decisions: { operator: 'allow', auditor: 'allow', strategist: 'allow' },
    review_choices: { operator: 'allow', auditor: 'allow', strategist: 'allow' },
    review_rationales: {
      operator: 'The command is read-only.', auditor: 'Evidence confirms bounded risk.',
      strategist: 'Unanimous one-time approval.',
    },
    rationale: 'The command is read-only; evidence confirms bounded risk.',
    pre_action_fingerprint: 'c'.repeat(64), post_action_fingerprint: 'd'.repeat(64),
    executed_at: now, completed_at: now,
  }
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    if (String(input) === '/api/snapshot') {
      return new Response(JSON.stringify({ ...snapshot, prompts: [reviewed] }), { status: 200 })
    }
    return new Response('{}', { status: 200 })
  })

  render(<App />)
  await user.click(await screen.findByRole('button', { name: /Approvals 0/ }))
  await user.click(screen.getByRole('button', { name: /Council audit 1/ }))

  expect(screen.getByText('Council decision trail')).toBeInTheDocument()
  expect(screen.getByText('operator')).toBeInTheDocument()
  expect(screen.getByText('Evidence confirms bounded risk.')).toBeInTheDocument()
  expect(screen.getByText(/Pane cccccccc → dddddddd/)).toBeInTheDocument()
})

test('council memory candidates are visible and can be activated', async () => {
  const user = userEvent.setup()
  const candidate = candidateMemory()
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/snapshot') {
      return new Response(JSON.stringify({ ...snapshot, memories: [candidate] }), { status: 200 })
    }
    if (url === '/api/memories/memory-candidate/activate') {
      return new Response(JSON.stringify({ ...candidate, status: 'active' }), { status: 200 })
    }
    return new Response('{}', { status: 200 })
  })

  render(<App />)
  await user.click(await screen.findByRole('button', { name: /Memories 1/ }))
  expect(screen.getByRole('dialog', { name: 'Council memories' })).toHaveTextContent('candidate')
  expect(screen.getByText(candidate.claim)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Activate' }))
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(
    '/api/memories/memory-candidate/activate', expect.objectContaining({ method: 'POST' }),
  ))
  await waitFor(() => expect(screen.getByRole('dialog', { name: 'Council memories' })).toHaveTextContent('active'))
})

afterEach(() => vi.unstubAllGlobals())

test('live inventory and wall activity cannot steal focus or overwrite a chat draft', async () => {
  const user = userEvent.setup()
  render(<App />)
  const composer = await screen.findByLabelText('Council message')
  await user.click(composer)
  await user.type(composer, 'permission for deploy?')

  act(() => {
    FakeEventSource.instance.emit('workers', { workers: [worker(), worker('worker-two', 'Unrelated work')] })
    FakeEventSource.instance.emit('wall_event', {
      event_id: 'event-1', created_at: now, actor: 'observer', kind: 'state_changed',
      message: 'another worker changed', worker_id: 'worker-two', host: 'laptop', intent: null,
      severity: 'info', data: {},
    })
  })

  expect(composer).toHaveValue('permission for deploy?')
  expect(composer).toHaveFocus()
  expect(screen.getByText('another worker changed')).toBeInTheDocument()
})

test('Enter submits, Shift+Enter adds a line, and failures preserve the draft', async () => {
  const user = userEvent.setup()
  render(<App />)
  const composer = await screen.findByLabelText('Council message')
  await user.type(composer, 'first line{shift>}{enter}{/shift}second line')
  expect(composer).toHaveValue('first line\nsecond line')
  await user.type(composer, '{enter}')
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/chat', expect.objectContaining({ method: 'POST' })))
  await waitFor(() => expect(composer).toHaveValue(''))

  vi.mocked(fetch).mockRejectedValueOnce(new Error('council unavailable'))
  await user.type(composer, 'keep this{enter}')
  expect(await screen.findByRole('alert')).toHaveTextContent('council unavailable')
  expect(composer).toHaveValue('keep this')
})

test('worker selection survives unrelated updates and stream state is visible', async () => {
  const user = userEvent.setup()
  render(<App />)
  await user.click(await screen.findByText('Build the control plane'))
  expect(screen.getByRole('dialog', { name: 'Worker inspector' })).toHaveTextContent('Build the control plane')

  act(() => FakeEventSource.instance.emit('workers', { workers: [worker(), worker('worker-two')] }))
  expect(screen.getByRole('dialog', { name: 'Worker inspector' })).toHaveTextContent('Build the control plane')

  act(() => FakeEventSource.instance.emit('ready', { connected: true }))
  expect(screen.getByText('Live')).toBeInTheDocument()
  act(() => FakeEventSource.instance.onerror?.())
  expect(screen.getByRole('status')).toHaveTextContent('Control-plane stream unavailable')

  const callsBeforeResync = vi.mocked(fetch).mock.calls.length
  act(() => FakeEventSource.instance.emit('resync', { reason: 'client_buffer_overflow' }))
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.length).toBeGreaterThan(callsBeforeResync))
})

test('council lifecycle stays visible without disabling a new draft', async () => {
  const user = userEvent.setup()
  render(<App />)
  const composer = await screen.findByLabelText('Council message')
  act(() => {
    FakeEventSource.instance.emit('controller_state', {
      controller_id: 'operator', role: 'operator', harness: 'claude.vertex', model: 'sonnet',
      status: 'busy', current_notification_id: 'notice-1', cycles_completed: 2,
      restart_count: 0, last_error: null,
    })
    FakeEventSource.instance.emit('council_notification', {
      notification_id: 'notice-1', reason: 'human_question', priority: 100,
      target_roles: ['operator'], worker_id: null, human_message: 'What is the goal?',
      status: 'running', attempts: 1, summary: null, answer: null, answered_by: null,
      error: null, created_at: now,
    })
  })
  expect(screen.getByText('Council investigating your question')).toBeInTheDocument()
  expect(screen.getByText('operator: busy')).toBeInTheDocument()
  await user.type(composer, 'another question')
  expect(composer).toHaveValue('another question')

  act(() => FakeEventSource.instance.emit('council_notification', {
    notification_id: 'notice-1', reason: 'human_question', priority: 100,
    target_roles: ['operator'], worker_id: null, human_message: 'What is the goal?',
    status: 'timed_out', attempts: 1, summary: null, answer: null, answered_by: null,
    error: 'deadline exceeded', created_at: now,
  }))
  expect(screen.getByRole('alert')).toHaveTextContent('Council timed out')
  expect(screen.getByText('Retry')).toBeInTheDocument()

  act(() => FakeEventSource.instance.emit('council_notification', {
    notification_id: 'notice-1', reason: 'human_question', priority: 100,
    target_roles: ['operator'], worker_id: null, human_message: 'What is the goal?',
    status: 'completed', attempts: 2, summary: 'answered', answer: 'The goal',
    answered_by: 'operator', error: null, created_at: now,
  }))
  expect(screen.getByText('Council completed')).toBeInTheDocument()
  expect(screen.getByText('answered by operator')).toBeInTheDocument()
})
