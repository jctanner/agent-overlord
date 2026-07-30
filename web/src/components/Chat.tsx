import { memo, useEffect, useRef, useState } from 'react'
import type { ChatMessage, ControllerState, CouncilNotification } from '../types'

interface Props {
  messages: ChatMessage[]
  onSend: (message: string) => Promise<void>
  controllers: ControllerState[]
  notifications: CouncilNotification[]
}

function ChatView({ messages, onSend, controllers, notifications }: Props) {
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const historyRef = useRef<HTMLDivElement>(null)
  const active = notifications.filter((item) =>
    item.human_message && (item.status === 'pending' || item.status === 'running'))
  const terminal = notifications.find((item) =>
    item.human_message && (item.status === 'failed' || item.status === 'timed_out'))
  const completed = notifications.find((item) =>
    item.human_message && item.status === 'completed')

  useEffect(() => {
    if (historyRef.current) historyRef.current.scrollTop = historyRef.current.scrollHeight
  }, [messages])

  async function submit() {
    const message = draft.trim()
    if (!message || pending) return
    setPending(true)
    setError(null)
    try {
      await onSend(message)
      setDraft('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setPending(false)
    }
  }

  return (
    <section className="panel chat-panel" aria-labelledby="chat-title">
      <header className="panel-heading compact">
        <div><p className="eyebrow">Human direction</p><h2 id="chat-title">Control council</h2></div>
        <div className="council-health" aria-label="Controller health">
          {controllers.map((controller) => (
            <span key={controller.controller_id} className={`controller-${controller.status}`} title={controller.last_error ?? `${controller.harness} · ${controller.model}`}>
              <i />{controller.role}: {controller.status}
            </span>
          ))}
          {!controllers.length && <span className="chat-state">Council disabled</span>}
        </div>
      </header>
      {!!active.length && (
        <div className="council-activity" role="status">
          <span className="activity-pulse" />
          Council investigating {active.length === 1 ? 'your question' : `${active.length} questions`}
          <small>{active[0].status} · attempt {active[0].attempts || 1}</small>
        </div>
      )}
      {terminal && (
        <div className="council-failure" role="alert">
          <span>{terminal.status === 'timed_out' ? 'Council timed out' : 'Council failed'}: {terminal.error}</span>
          <button onClick={() => terminal.human_message && void onSend(terminal.human_message)}>Retry</button>
        </div>
      )}
      {!active.length && !terminal && completed && (
        <div className="council-completed" role="status">
          Council completed
          <small>{completed.answered_by ? `answered by ${completed.answered_by}` : 'answer recorded'}</small>
        </div>
      )}
      <div className="chat-history" ref={historyRef}>
        {messages.map((message, index) => (
          <article className={`chat-message role-${message.role}`} key={`${index}-${message.role}`}>
            <strong>{message.role === 'user' ? 'You' : 'Council'}</strong>
            <p>{message.message}</p>
          </article>
        ))}
      </div>
      <div className="composer">
        <textarea
          aria-label="Council message"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              void submit()
            }
          }}
          placeholder="Ask or instruct the council…"
          rows={2}
        />
        <button onClick={() => void submit()} disabled={pending || !draft.trim()}>Send</button>
      </div>
      {error && <p role="alert" className="request-error">{error}</p>}
    </section>
  )
}

export const Chat = memo(ChatView)
