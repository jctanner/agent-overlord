import { memo, useMemo, useState } from 'react'
import type { AutomationSettings, Health, PromptRequest, Worker } from '../types'

const stateOrder: Record<string, number> = {
  awaiting_input: 0,
  failed: 1,
  stalled: 2,
  disconnected: 3,
  active: 4,
  idle: 5,
  complete: 6,
  unknown: 7,
}

interface Props {
  workers: Worker[]
  health: Health | null
  selectedId: string | null
  onSelect: (id: string) => void
  prompts?: PromptRequest[]
  automation?: AutomationSettings
  onAutoYes?: (workerId: string, enabled: boolean) => Promise<void>
}

type SortKey = 'attention' | 'host' | 'tmux' | 'purpose' | 'harness' | 'model' | 'context' | 'state' | 'recent'
type SortDirection = 'asc' | 'desc'

const sortLabels: Record<SortKey, string> = {
  attention: 'Attention first',
  host: 'Host',
  tmux: 'Tmux session',
  purpose: 'Purpose',
  harness: 'Harness',
  model: 'Model',
  context: 'Context',
  state: 'State',
  recent: 'Recent activity',
}

function text(value: string | null | undefined): string {
  return value?.toLocaleLowerCase() ?? ''
}

function contextValue(value: string): number {
  const match = value.match(/[\d.]+/)
  return match ? Number(match[0]) : -1
}

function compareWorkers(a: Worker, b: Worker, key: SortKey): number {
  switch (key) {
    case 'attention': return (stateOrder[a.state] ?? 99) - (stateOrder[b.state] ?? 99)
    case 'host': return text(a.observation.host).localeCompare(text(b.observation.host))
    case 'tmux': return text(a.observation.display_name).localeCompare(text(b.observation.display_name))
    case 'purpose': return text(a.purpose).localeCompare(text(b.purpose))
    case 'harness': return text(a.harness).localeCompare(text(b.harness))
    case 'model': return text(a.model).localeCompare(text(b.model))
    case 'context': return contextValue(a.context) - contextValue(b.context)
    case 'state': return text(a.state).localeCompare(text(b.state))
    case 'recent': return Date.parse(a.last_seen_at) - Date.parse(b.last_seen_at)
  }
}

function SessionTableView({ workers, health, selectedId, onSelect, prompts = [], automation, onAutoYes }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('attention')
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc')
  const ordered = useMemo(() => [...workers].sort((a, b) => {
    const primary = compareWorkers(a, b, sortKey)
    const directed = sortDirection === 'asc' ? primary : -primary
    return directed ||
      text(a.observation.host).localeCompare(text(b.observation.host)) ||
      text(a.observation.display_name).localeCompare(text(b.observation.display_name)) ||
      a.worker_id.localeCompare(b.worker_id)
  }), [workers, sortKey, sortDirection])

  function chooseSort(key: SortKey) {
    if (key === sortKey) {
      setSortDirection((current) => current === 'asc' ? 'desc' : 'asc')
      return
    }
    setSortKey(key)
    setSortDirection(key === 'recent' ? 'desc' : 'asc')
  }

  function sortHeader(label: string, key: SortKey) {
    const active = sortKey === key
    return (
      <button
        className={active ? 'sort-header active' : 'sort-header'}
        onClick={() => chooseSort(key)}
        aria-label={`Sort by ${label}${active ? `, currently ${sortDirection === 'asc' ? 'ascending' : 'descending'}` : ''}`}
      >
        {label}<span aria-hidden="true">{active ? (sortDirection === 'asc' ? '▲' : '▼') : '↕'}</span>
      </button>
    )
  }
  const awaiting = workers.filter((worker) => worker.awaiting_input).length
  const confirmedPromptWorkers = new Set(
    prompts.filter((item) => !['succeeded', 'rejected', 'stale', 'failed', 'expired'].includes(item.status)).map((item) => item.worker_id),
  )
  const rateLimitedWorkers = new Set(
    prompts.filter((item) => item.error?.startsWith('rate limited until ')).map((item) => item.worker_id),
  )
  const troubled = workers.filter((worker) =>
    ['failed', 'stalled', 'disconnected'].includes(worker.state),
  ).length

  return (
    <section className="panel sessions-panel" aria-labelledby="sessions-title">
      <header className="panel-heading">
        <div>
          <p className="eyebrow">Fleet inventory</p>
          <h2 id="sessions-title">Agent sessions</h2>
        </div>
        <div className="session-tools">
          <div className="fleet-summary">
            <span>{health?.configured_hosts ?? '—'} hosts</span>
            <span>{workers.length} workers</span>
            <span className="attention">{awaiting} awaiting</span>
            <span className="attention">{confirmedPromptWorkers.size} confirmed prompts</span>
            <span className={troubled ? 'danger' : ''}>{troubled} troubled</span>
          </div>
          <label className="sort-control">
            <span>Sort</span>
            <select value={sortKey} onChange={(event) => chooseSort(event.target.value as SortKey)}>
              {Object.entries(sortLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
            </select>
          </label>
          <button
            className="sort-direction"
            onClick={() => setSortDirection((current) => current === 'asc' ? 'desc' : 'asc')}
            aria-label={`Sort ${sortDirection === 'asc' ? 'descending' : 'ascending'}`}
            title="Reverse sort order"
          >{sortDirection === 'asc' ? '▲' : '▼'}</button>
        </div>
      </header>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>{sortHeader('Host', 'host')}</th>
              <th>{sortHeader('Tmux', 'tmux')}</th>
              <th>{sortHeader('Purpose', 'purpose')}</th>
              <th>{sortHeader('Harness', 'harness')}</th>
              <th>{sortHeader('Model', 'model')}</th>
              <th>{sortHeader('Context', 'context')}</th>
              <th>{sortHeader('State', 'state')}</th>
              <th>Automation</th>
            </tr>
          </thead>
          <tbody>
            {ordered.map((worker) => (
              <tr
                key={worker.worker_id}
                className={selectedId === worker.worker_id ? 'selected' : ''}
                onClick={() => onSelect(worker.worker_id)}
                tabIndex={0}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') onSelect(worker.worker_id)
                }}
              >
                <td>{worker.observation.host}</td>
                <td className="mono">{worker.observation.display_name}</td>
                <td title={worker.purpose}>{worker.purpose}</td>
                <td>{worker.harness}</td>
                <td>{worker.model}</td>
                <td>{worker.context}</td>
                <td><span className={`state state-${worker.state}`}>{rateLimitedWorkers.has(worker.worker_id) ? 'rate limited' : worker.state === 'awaiting_input' ? (confirmedPromptWorkers.has(worker.worker_id) ? 'confirmed prompt' : 'attention hint') : worker.state.replace('_', ' ')}</span></td>
                <td>
                  {automation && onAutoYes && (() => {
                    const enabled = automation.auto_yes_workers.includes(worker.worker_id)
                    return <button
                      className={`auto-yes ${enabled ? 'active' : ''}`}
                      aria-pressed={enabled}
                      title={enabled
                        ? 'Turn off automatic yes responses for this worker pane'
                        : 'Automatically approve recognized routine and elevated prompts in this worker pane'}
                      onClick={(event) => {
                        event.stopPropagation()
                        void onAutoYes(worker.worker_id, !enabled)
                      }}
                    >{enabled ? 'Auto yes: on' : 'Auto yes'}</button>
                  })()}
                </td>
              </tr>
            ))}
            {!ordered.length && (
              <tr><td colSpan={8} className="empty">No agent panes observed yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export const SessionTable = memo(SessionTableView)
