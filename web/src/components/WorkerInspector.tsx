import { useState } from 'react'
import type { Worker } from '../types'

interface Props {
  worker: Worker | null
  onClose: () => void
  onForget: (worker: Worker) => Promise<void>
  onIgnoreSession: (worker: Worker) => Promise<void>
}

export function WorkerInspector({ worker, onClose, onForget, onIgnoreSession }: Props) {
  const [pending, setPending] = useState<'forget' | 'ignore' | null>(null)
  const [error, setError] = useState<string | null>(null)
  if (!worker) return null

  async function act(kind: 'forget' | 'ignore', operation: () => Promise<void>) {
    setPending(kind)
    setError(null)
    try {
      await operation()
      onClose()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setPending(null)
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
    <section className="worker-modal" role="dialog" aria-modal="true" aria-label="Worker inspector">
      <header>
        <div><p className="eyebrow">Worker detail</p><h2>{worker.observation.host}/{worker.observation.display_name}</h2></div>
        <button aria-label="Close worker inspector" onClick={onClose}>×</button>
      </header>
      <dl className="worker-fields">
        <div><dt>State</dt><dd><span className={`state state-${worker.state}`}>{worker.state}</span></dd></div>
        <div><dt>Purpose</dt><dd>{worker.purpose}</dd></div>
        <div><dt>Confidence</dt><dd>{Math.round(worker.confidence * 100)}%</dd></div>
        <div><dt>Harness / model</dt><dd>{worker.harness} / {worker.model}</dd></div>
        <div><dt>Context</dt><dd>{worker.context}</dd></div>
        <div><dt>Working directory</dt><dd className="mono">{worker.observation.current_path || 'unknown'}</dd></div>
        <div><dt>Command</dt><dd className="mono">{worker.observation.current_command || 'unknown'}</dd></div>
        <div><dt>Worker ID</dt><dd className="mono">{worker.worker_id}</dd></div>
      </dl>
      <h3>Evidence</h3>
      <ul>{worker.evidence.map((item) => <li key={item}>{item}</li>)}</ul>
      <h3>Recent pane output</h3>
      <pre className="pane-output">{worker.observation.content.join('\n') || '(no captured output)'}</pre>
      <section className="worker-actions" aria-label="Worker lifecycle actions">
        <h3>Inventory actions</h3>
        <p>These actions change Agent Overlord's inventory only. They never send input to tmux.</p>
        <div>
          {worker.state === 'disconnected' && (
            <button
              className="danger-button"
              disabled={pending !== null}
              onClick={() => {
                if (window.confirm(`Permanently forget ${worker.observation.display_name}?`)) {
                  void act('forget', () => onForget(worker))
                }
              }}
            >{pending === 'forget' ? 'Forgetting…' : 'Permanently gone'}</button>
          )}
          <button
            disabled={pending !== null}
            onClick={() => {
              if (window.confirm(`Ignore every pane in tmux session ${worker.observation.session_name}?`)) {
                void act('ignore', () => onIgnoreSession(worker))
              }
            }}
          >{pending === 'ignore' ? 'Ignoring…' : 'Ignore tmux session'}</button>
        </div>
        {error && <p role="alert" className="request-error">{error}</p>}
      </section>
    </section>
    </div>
  )
}
