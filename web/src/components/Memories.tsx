import { useState } from 'react'
import type { Memory } from '../types'

interface Props {
  open: boolean
  memories: Memory[]
  onClose: () => void
  onCreate: (claim: string, scope: string) => Promise<void>
  onUpdate: (id: string, claim: string) => Promise<void>
  onActivate: (id: string) => Promise<void>
  onDelete: (id: string) => Promise<void>
}

export function Memories({ open, memories, onClose, onCreate, onUpdate, onActivate, onDelete }: Props) {
  const [claim, setClaim] = useState('')
  const [scope, setScope] = useState('global')
  const [busy, setBusy] = useState(false)
  if (!open) return null

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <section className="memory-modal" role="dialog" aria-modal="true" aria-labelledby="memory-title">
        <header><div><p className="eyebrow">Shared knowledge</p><h2 id="memory-title">Council memories</h2></div><button onClick={onClose}>×</button></header>
        <form onSubmit={async (event) => {
          event.preventDefault()
          if (!claim.trim()) return
          setBusy(true)
          try { await onCreate(claim.trim(), scope.trim() || 'global'); setClaim('') } finally { setBusy(false) }
        }}>
          <input aria-label="Memory scope" value={scope} onChange={(e) => setScope(e.target.value)} />
          <input aria-label="Memory claim" value={claim} onChange={(e) => setClaim(e.target.value)} placeholder="What should the council remember?" />
          <button disabled={busy || !claim.trim()}>Remember</button>
        </form>
        <div className="memory-list">
          {memories.map((memory) => (
            <article key={memory.memory_id}>
              <div><span>{memory.status}</span><span>{memory.scope}</span><span>{memory.kind}</span><code>{memory.memory_id.slice(0, 8)}</code></div>
              <p>{memory.claim}</p>
              <footer>
                {memory.status === 'candidate' && <button onClick={() => void onActivate(memory.memory_id)}>Activate</button>}
                <button onClick={async () => {
                  const replacement = window.prompt('Correct this memory', memory.claim)
                  if (replacement?.trim()) await onUpdate(memory.memory_id, replacement.trim())
                }}>Correct</button>
                <button className="danger-button" onClick={() => void onDelete(memory.memory_id)}>{memory.status === 'candidate' ? 'Reject' : 'Forget'}</button>
              </footer>
            </article>
          ))}
          {!memories.length && <p className="empty">No shared memories.</p>}
        </div>
      </section>
    </div>
  )
}
