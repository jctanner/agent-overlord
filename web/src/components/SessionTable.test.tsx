import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { SessionTable } from './SessionTable'
import type { AutomationSettings, PromptRequest, Worker, WorkerState } from '../types'

afterEach(cleanup)

function worker(id: string, host: string, state: WorkerState, purpose: string, seen: string): Worker {
  return {
    worker_id: id,
    observation: {
      host, tmux_socket: 'default', session_id: '$1', session_name: 'work', window_id: '@1',
      window_name: id, pane_id: `%${id}`, pane_index: 0, pane_title: '', current_path: '/work',
      current_command: 'codex', start_command: 'codex', descendant_commands: [],
      content: [], observed_at: seen,
      display_name: `work:${id}.0`, content_fingerprint: id,
    },
    harness: 'codex', model: 'gpt-5', context: '80%', purpose, project: null, state,
    awaiting_input: state === 'awaiting_input', input_kind: null, confidence: 1, evidence: [],
    first_seen_at: seen, last_seen_at: seen, unchanged_since: seen,
  }
}

const workers = [
  worker('idle', 'z-host', 'idle', 'Third', '2026-07-16T10:00:00Z'),
  worker('failed', 'a-host', 'failed', 'Second', '2026-07-16T12:00:00Z'),
  worker('waiting', 'z-host', 'awaiting_input', 'First', '2026-07-16T11:00:00Z'),
]

function rowIds(): string[] {
  return screen.getAllByRole('row').slice(1).map((row) => within(row).getAllByRole('cell')[2].textContent ?? '')
}

test('defaults to attention order and supports explicit stable sorts', async () => {
  const user = userEvent.setup()
  render(<SessionTable workers={workers} health={null} selectedId={null} onSelect={vi.fn()} />)

  expect(rowIds()).toEqual(['First', 'Second', 'Third'])

  await user.click(screen.getByRole('button', { name: /Sort by Host/ }))
  expect(rowIds()).toEqual(['Second', 'Third', 'First'])

  await user.click(screen.getByRole('button', { name: /Sort by Host, currently ascending/ }))
  expect(rowIds()).toEqual(['Third', 'First', 'Second'])

  await user.selectOptions(screen.getByRole('combobox', { name: /Sort/ }), 'recent')
  expect(rowIds()).toEqual(['Second', 'First', 'Third'])
})

test('offers a session-scoped auto yes toggle without selecting the row', async () => {
  const user = userEvent.setup()
  const onSelect = vi.fn()
  const onAutoYes = vi.fn(async () => undefined)
  const automation: AutomationSettings = {
    automation_enabled: false, dry_run: true, paused: false,
    disabled_hosts: [], disabled_projects: [], disabled_sessions: [], disabled_workers: [],
    auto_yes_workers: [], prompt_expiration_secs: 120, verification_timeout_secs: 8,
    max_actions_per_pane_per_hour: 20, updated_at: '2026-07-16T12:00:00Z',
    auto_yes_max_actions_per_worker_per_hour: 100,
    review_precedent_ttl_secs: 604800,
  }
  render(<SessionTable
    workers={[workers[0]]} health={null} selectedId={null} onSelect={onSelect}
    automation={automation} onAutoYes={onAutoYes}
  />)

  await user.click(screen.getByRole('button', { name: 'Auto yes' }))

  expect(onAutoYes).toHaveBeenCalledWith('idle', true)
  expect(onSelect).not.toHaveBeenCalled()
})

test('shows a persisted auto yes rate limit in the affected worker row', () => {
  const prompt = {
    worker_id: 'idle',
    status: 'decided',
    error: 'rate limited until 2026-07-16T13:00:00+00:00',
  } as PromptRequest

  render(<SessionTable
    workers={[workers[0]]} health={null} selectedId={null} onSelect={vi.fn()}
    prompts={[prompt]}
  />)

  expect(screen.getByText('rate limited')).toBeInTheDocument()
})
