import { memo, useEffect, useMemo, useRef, useState } from 'react'
import type { WallEvent } from '../types'

interface Props { events: WallEvent[] }

function WallView({ events }: Props) {
  const [follow, setFollow] = useState(true)
  const [kind, setKind] = useState('all')
  const [query, setQuery] = useState('')
  const logRef = useRef<HTMLDivElement>(null)

  const kinds = useMemo(
    () => ['all', ...Array.from(new Set(events.map((event) => event.kind))).sort()],
    [events],
  )
  const filtered = useMemo(() => {
    const needle = query.toLowerCase().trim()
    return events.filter((event) =>
      (kind === 'all' || event.kind === kind) &&
      (!needle || `${event.actor} ${event.host ?? ''} ${event.message}`.toLowerCase().includes(needle)),
    )
  }, [events, kind, query])

  useEffect(() => {
    if (follow && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [filtered, follow])

  return (
    <section className="panel wall-panel" aria-labelledby="wall-title">
      <header className="panel-heading compact">
        <div>
          <p className="eyebrow">Shared activity</p>
          <h2 id="wall-title">Council wall</h2>
        </div>
        <div className="wall-controls">
          <input aria-label="Search wall" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search" />
          <select aria-label="Filter wall events" value={kind} onChange={(e) => setKind(e.target.value)}>
            {kinds.map((value) => <option key={value}>{value}</option>)}
          </select>
          <button className={follow ? 'active' : ''} onClick={() => setFollow((value) => !value)}>{follow ? 'Following' : 'Paused'}</button>
          <button onClick={() => { setFollow(true); logRef.current?.scrollTo({ top: logRef.current.scrollHeight }) }}>Latest</button>
        </div>
      </header>
      <div className="wall-log" ref={logRef} aria-label="Council wall activity">
        {filtered.map((event) => (
          <details className={`wall-line severity-${event.severity}`} key={event.event_id}>
            <summary>
              <time>{new Date(event.created_at).toLocaleTimeString()}</time>
              <strong>{event.actor}</strong>
              <span className="event-kind">{event.kind}</span>
              <span>{event.message}</span>
            </summary>
            <pre>{JSON.stringify(event, null, 2)}</pre>
          </details>
        ))}
        {!filtered.length && <p className="empty">No matching wall events.</p>}
      </div>
    </section>
  )
}

export const Wall = memo(WallView)
