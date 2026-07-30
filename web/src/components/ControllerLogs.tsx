import { useEffect, useRef } from 'react'
import type { ControllerState } from '../types'

interface Props {
  open: boolean
  controllers: ControllerState[]
  entries: string[]
  selectedId: string
  onSelect: (id: string) => void
  onClose: () => void
}

export function ControllerLogs({ open, controllers, entries, selectedId, onSelect, onClose }: Props) {
  const scrollRef = useRef<HTMLPreElement>(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [entries])

  if (!open) return null

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <section className="controller-logs-modal" role="dialog" aria-modal="true">
        <header>
          <div>
            <p className="eyebrow">Diagnostics</p>
            <h2>Controller logs</h2>
          </div>
          <div className="logs-controls">
            <select value={selectedId} onChange={(event) => onSelect(event.target.value)}>
              {controllers.map((c) => (
                <option key={c.controller_id} value={c.controller_id}>{c.controller_id} ({c.role})</option>
              ))}
            </select>
            <button aria-label="Close controller logs" onClick={onClose}>×</button>
          </div>
        </header>
        <pre ref={scrollRef} className="logs-output">
          {entries.length ? entries.join('\n\n') : '(no log entries)'}
        </pre>
      </section>
    </div>
  )
}
