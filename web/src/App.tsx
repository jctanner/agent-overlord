import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, appendChat } from './api'
import { Chat } from './components/Chat'
import { ControllerLogs } from './components/ControllerLogs'
import { Memories } from './components/Memories'
import { SessionTable } from './components/SessionTable'
import { Wall } from './components/Wall'
import { WorkerInspector } from './components/WorkerInspector'
import { ApprovalCenter } from './components/ApprovalCenter'
import type { ApprovalPolicy, AutomationSettings, ChatMessage, ControllerState, CouncilNotification, Health, IgnoredSession, Memory, PromptRequest, Snapshot, WallEvent, Worker } from './types'

const defaultAutomation: AutomationSettings = {
  automation_enabled: false, dry_run: true, paused: false,
  disabled_hosts: [], disabled_projects: [], prompt_expiration_secs: 120,
  disabled_sessions: [], disabled_workers: [],
  auto_yes_workers: [],
  verification_timeout_secs: 8, max_actions_per_pane_per_hour: 20,
  auto_yes_max_actions_per_worker_per_hour: 100,
  review_precedent_ttl_secs: 604800,
  updated_at: new Date(0).toISOString(),
}

export default function App() {
  const [workers, setWorkers] = useState<Worker[]>([])
  const [events, setEvents] = useState<WallEvent[]>([])
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [memories, setMemories] = useState<Memory[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [controllers, setControllers] = useState<ControllerState[]>([])
  const [notifications, setNotifications] = useState<CouncilNotification[]>([])
  const [ignoredSessions, setIgnoredSessions] = useState<IgnoredSession[]>([])
  const [prompts, setPrompts] = useState<PromptRequest[]>([])
  const [policies, setPolicies] = useState<ApprovalPolicy[]>([])
  const [automation, setAutomation] = useState<AutomationSettings>(defaultAutomation)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [memoryOpen, setMemoryOpen] = useState(false)
  const [approvalsOpen, setApprovalsOpen] = useState(false)
  const [logsOpen, setLogsOpen] = useState(false)
  const [logEntries, setLogEntries] = useState<string[]>([])
  const [logControllerId, setLogControllerId] = useState('')
  const [connected, setConnected] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [restoringSessions, setRestoringSessions] = useState(false)

  const applySnapshot = useCallback((snapshot: Snapshot) => {
    setWorkers(snapshot.workers)
    setEvents(snapshot.events)
    setMessages(snapshot.messages)
    setMemories(snapshot.memories)
    setHealth(snapshot.health)
    setControllers(snapshot.controllers ?? [])
    setNotifications(snapshot.notifications ?? [])
    setIgnoredSessions(snapshot.ignored_sessions ?? [])
    setPrompts(snapshot.prompts ?? [])
    setPolicies(snapshot.policies ?? [])
    setAutomation(snapshot.automation ?? defaultAutomation)
  }, [])

  const reload = useCallback(async () => {
    try {
      applySnapshot(await api.snapshot())
      setLoadError(null)
    } catch (reason) {
      setLoadError(reason instanceof Error ? reason.message : String(reason))
    }
  }, [applySnapshot])

  useEffect(() => { void reload() }, [reload])

  useEffect(() => {
    const source = new EventSource('/api/stream')
    const listen = <T,>(name: string, handler: (data: T) => void) => {
      source.addEventListener(name, (event) => handler(JSON.parse((event as MessageEvent).data) as T))
    }
    listen<Snapshot>('snapshot', applySnapshot)
    listen<{ workers: Worker[] }>('workers', (data) => setWorkers(data.workers))
    listen<Health>('health', setHealth)
    listen<WallEvent>('wall_event', (event) => setEvents((current) =>
      current.some((item) => item.event_id === event.event_id) ? current : [...current.slice(-999), event],
    ))
    listen<ChatMessage>('chat_message', (message) => setMessages((current) => appendChat(current, message)))
    listen<ControllerState>('controller_state', (state) => setControllers((current) => {
      const exists = current.some((item) => item.controller_id === state.controller_id)
      return exists ? current.map((item) => item.controller_id === state.controller_id ? state : item) : [...current, state]
    }))
    listen<CouncilNotification>('council_notification', (notification) => setNotifications((current) => {
      const exists = current.some((item) => item.notification_id === notification.notification_id)
      return exists ? current.map((item) => item.notification_id === notification.notification_id ? notification : item) : [notification, ...current]
    }))
    listen<{ action: string; memory: Memory }>('memory', ({ action, memory }) => setMemories((current) => {
      if (action === 'deleted') return current.filter((item) => item.memory_id !== memory.memory_id)
      const exists = current.some((item) => item.memory_id === memory.memory_id)
      return exists ? current.map((item) => item.memory_id === memory.memory_id ? memory : item) : [memory, ...current]
    }))
    listen<PromptRequest>('prompt', (prompt) => setPrompts((current) => {
      const exists = current.some((item) => item.prompt_id === prompt.prompt_id)
      return exists ? current.map((item) => item.prompt_id === prompt.prompt_id ? prompt : item) : [prompt, ...current]
    }))
    listen<{ controller_id: string; entry: string }>('controller_log', ({ controller_id, entry }) => {
      setLogControllerId((currentId) => {
        if (currentId === controller_id) setLogEntries((current) => [...current, entry])
        return currentId
      })
    })
    listen<{ connected: boolean }>('ready', () => { setConnected(true); setLoadError(null) })
    listen<{ reason: string }>('resync', () => void reload())
    source.onerror = () => setConnected(false)
    return () => source.close()
  }, [applySnapshot, reload])

  const selected = useMemo(
    () => workers.find((worker) => worker.worker_id === selectedId) ?? null,
    [workers, selectedId],
  )

  const sendChat = useCallback(async (message: string) => {
    const response = await api.chat(message)
    if (response.notification_id) {
      setNotifications((current) => [{
        notification_id: response.notification_id!, reason: 'human_question', priority: 100,
        target_roles: [], worker_id: null, human_message: message, status: 'pending', attempts: 0,
        summary: null, answer: null, answered_by: null, error: null, created_at: new Date().toISOString(),
      }, ...current.filter((item) => item.notification_id !== response.notification_id)])
    }
  }, [])

  return (
    <div className="app-shell">
      <header className="topbar">
        <div><span className="mark">AO</span><div><h1>Agent Overlord</h1><p>Local tmux agent control plane</p></div></div>
        <nav>
          <button onClick={() => setApprovalsOpen(true)}>Approvals <span>{prompts.filter((item) => !['succeeded', 'rejected', 'stale', 'failed', 'expired'].includes(item.status)).length}</span></button>
          {ignoredSessions.length > 0 && (
            <button
              disabled={restoringSessions}
              onClick={async () => {
                setRestoringSessions(true)
                try {
                  const result = await api.restoreIgnoredSessions()
                  setIgnoredSessions([])
                  setWorkers(result.workers)
                  setLoadError(null)
                } catch (reason) {
                  setLoadError(reason instanceof Error ? reason.message : String(reason))
                } finally {
                  setRestoringSessions(false)
                }
              }}
            >
              {restoringSessions ? 'Restoring…' : 'Restore excluded'} <span>{ignoredSessions.length}</span>
            </button>
          )}
          <button onClick={() => setMemoryOpen(true)}>Memories <span>{memories.length}</span></button>
          {controllers.length > 0 && (
            <button onClick={async () => {
              const id = logControllerId || controllers[0]?.controller_id || ''
              if (!id) return
              setLogControllerId(id)
              try { const data = await api.controllerLogs(id); setLogEntries(data.entries) } catch { setLogEntries([]) }
              setLogsOpen(true)
            }}>Logs</button>
          )}
          <button onClick={() => void reload()}>Refresh view</button>
          <span className={`connection ${connected ? 'online' : 'offline'}`}><i />{connected ? 'Live' : 'Reconnecting'}</span>
        </nav>
      </header>
      {(!connected || loadError) && (
        <div className="connection-banner" role="status">
          <strong>Control-plane stream unavailable.</strong> Inventory continues in the service; this view will reconnect automatically.
          {loadError && <span>{loadError}</span>}
        </div>
      )}
      <main>
        <SessionTable
          workers={workers} prompts={prompts} health={health} selectedId={selectedId}
          onSelect={setSelectedId} automation={automation}
          onAutoYes={async (workerId, enabled) => {
            const workers = enabled
              ? [...new Set([...automation.auto_yes_workers, workerId])]
              : automation.auto_yes_workers.filter((item) => item !== workerId)
            setAutomation(await api.updateAutomation({
              auto_yes_workers: workers,
              ...(enabled ? { automation_enabled: true, dry_run: false } : {}),
            }))
          }}
        />
        <Wall events={events} />
        <Chat messages={messages} controllers={controllers} notifications={notifications} onSend={sendChat} />
      </main>
      <WorkerInspector
        worker={selected}
        onClose={() => setSelectedId(null)}
        onForget={async (worker) => {
          await api.forgetWorker(worker.worker_id)
          setWorkers((current) => current.filter((item) => item.worker_id !== worker.worker_id))
        }}
        onIgnoreSession={async (worker) => {
          const result = await api.ignoreWorkerSession(worker.worker_id)
          const removed = new Set(result.removed_worker_ids)
          setWorkers((current) => current.filter((item) => !removed.has(item.worker_id)))
        }}
      />
      <Memories
        open={memoryOpen}
        memories={memories}
        onClose={() => setMemoryOpen(false)}
        onCreate={async (claim, scope) => { const memory = await api.createMemory(claim, scope); setMemories((current) => [memory, ...current.filter((item) => item.memory_id !== memory.memory_id)]) }}
        onUpdate={async (id, claim) => { const memory = await api.updateMemory(id, claim); setMemories((current) => current.map((item) => item.memory_id === id ? memory : item)) }}
        onActivate={async (id) => { const memory = await api.activateMemory(id); setMemories((current) => current.map((item) => item.memory_id === id ? memory : item)) }}
        onDelete={async (id) => { await api.deleteMemory(id); setMemories((current) => current.filter((item) => item.memory_id !== id)) }}
      />
      <ControllerLogs
        open={logsOpen}
        controllers={controllers}
        entries={logEntries}
        selectedId={logControllerId}
        onSelect={async (id) => {
          setLogControllerId(id)
          try { const data = await api.controllerLogs(id); setLogEntries(data.entries) } catch { setLogEntries([]) }
        }}
        onClose={() => setLogsOpen(false)}
      />
      <ApprovalCenter
        open={approvalsOpen}
        prompts={prompts}
        policies={policies}
        automation={automation}
        controllers={controllers}
        onClose={() => setApprovalsOpen(false)}
        onDecision={async (prompt, choice) => {
          const decision = choice === 'deny' ? 'deny' : 'allow'
          const updated = await api.decidePrompt(
            prompt.prompt_id, decision, choice, prompt.observation_fingerprint,
            prompt.worker_id, prompt.pane_id,
            !automation.dry_run,
          )
          setPrompts((current) => current.map((item) => item.prompt_id === updated.prompt_id ? updated : item))
        }}
        onReview={async (prompt, tier) => {
          await api.reviewPrompt(prompt.prompt_id, tier)
          setPrompts((current) => current.map((item) => item.prompt_id === prompt.prompt_id ? { ...item, status: 'evaluating', tier } : item))
        }}
        onCreatePolicy={async (prompt, choice) => {
          const policy = await api.createPromptPolicy(prompt, choice)
          setPolicies((current) => [policy, ...current.filter((item) => item.policy_id !== policy.policy_id)])
        }}
        onRevokePolicy={async (policy) => {
          const updated = await api.revokePolicy(policy.policy_id)
          setPolicies((current) => current.map((item) => item.policy_id === updated.policy_id ? updated : item))
        }}
        onActivatePolicy={async (policy) => {
          const updated = await api.activatePolicy(policy.policy_id)
          setPolicies((current) => current.map((item) => item.policy_id === updated.policy_id ? updated : item))
        }}
        onAutomation={async (patch) => setAutomation(await api.updateAutomation(patch))}
      />
    </div>
  )
}
