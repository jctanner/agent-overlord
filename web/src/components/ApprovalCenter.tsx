import { useMemo, useState } from 'react'
import type { ApprovalPolicy, AutomationSettings, ControllerState, PromptRequest } from '../types'

interface Props {
  open: boolean
  prompts: PromptRequest[]
  policies: ApprovalPolicy[]
  automation: AutomationSettings
  controllers: ControllerState[]
  onClose: () => void
  onDecision: (prompt: PromptRequest, choice: string) => Promise<void>
  onReview: (prompt: PromptRequest, tier: 'fast' | 'council') => Promise<void>
  onCreatePolicy: (prompt: PromptRequest, choice: string) => Promise<void>
  onRevokePolicy: (policy: ApprovalPolicy) => Promise<void>
  onActivatePolicy: (policy: ApprovalPolicy) => Promise<void>
  onAutomation: (patch: Partial<AutomationSettings>) => Promise<void>
}

const terminal = new Set(['succeeded', 'rejected', 'stale', 'failed', 'expired'])
const age = (createdAt: string) => {
  const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(createdAt)) / 1000))
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m`
}
const timestamp = (value?: string | null) => value
  ? new Date(value).toLocaleString([], { dateStyle: 'medium', timeStyle: 'medium' })
  : '—'

export function ApprovalCenter(props: Props) {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [view, setView] = useState<'queue' | 'audit'>('queue')
  const pending = useMemo(
    () => props.prompts.filter((item) => !terminal.has(item.status)), [props.prompts],
  )
  const councilAudit = useMemo(
    () => props.prompts.filter((item) =>
      item.decision_source === 'fast'
      || item.decision_source === 'council'
      || Boolean(item.review_notification_id)
      || Object.keys(item.review_decisions ?? {}).length > 0,
    ), [props.prompts],
  )
  if (!props.open) return null

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key); setError(null)
    try { await action() } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally { setBusy(null) }
  }

  return (
    <div className="modal-backdrop">
      <section className="approval-modal" role="dialog" aria-label="Prompt approvals">
        <header><div><h2>Prompt approvals</h2><p>Policy, review, and verified pane actions</p></div><button onClick={props.onClose}>×</button></header>
        {error && <div className="approval-error" role="alert">{error}</div>}
        <nav className="approval-tabs" aria-label="Approval center views">
          <button aria-current={view === 'queue' ? 'page' : undefined} onClick={() => setView('queue')}>Queue &amp; policies <span>{pending.length}</span></button>
          <button aria-current={view === 'audit' ? 'page' : undefined} onClick={() => setView('audit')}>Council audit <span>{councilAudit.length}</span></button>
        </nav>
        {view === 'queue' && <>
        <div className="automation-strip">
          <label><input type="checkbox" checked={props.automation.automation_enabled} onChange={(event) => void run('automation', () => props.onAutomation({ automation_enabled: event.target.checked }))} /> Automation</label>
          <label><input type="checkbox" checked={props.automation.dry_run} onChange={(event) => void run('dry-run', () => props.onAutomation({ dry_run: event.target.checked }))} /> Dry run</label>
          <button className={props.automation.paused ? 'danger-button' : ''} onClick={() => void run('pause', () => props.onAutomation({ paused: !props.automation.paused }))}>{props.automation.paused ? 'Resume actions' : 'Pause actions'}</button>
          <label>Auto yes/hour <input
            type="number" min="1" max="1000"
            value={props.automation.auto_yes_max_actions_per_worker_per_hour}
            onChange={(event) => {
              const value = Number(event.target.value)
              if (Number.isInteger(value) && value > 0) void run('auto-yes-limit', () => props.onAutomation({ auto_yes_max_actions_per_worker_per_hour: value }))
            }}
          /></label>
          <span>{props.automation.dry_run ? 'No pane input will be sent' : 'Live bounded input enabled'}</span>
        </div>
        <div className="approval-columns">
          <div className="approval-list">
            <h3>Needs attention <span>{pending.length}</span></h3>
            {pending.length === 0 && <p className="empty-copy">No current prompts.</p>}
            {pending.map((prompt) => (
              <article className={`prompt-card risk-${prompt.risk}`} key={prompt.prompt_id}>
                <div className="prompt-meta"><strong>{prompt.host}:{prompt.window_name}.{prompt.pane_index}</strong><span>{prompt.risk}</span><span>{prompt.status}</span><span>{prompt.tier}</span><span>{age(prompt.created_at)} old</span></div>
                <code>{prompt.operation}</code>
                <p>{prompt.risk_reasons.join(' · ')}</p>
                {prompt.decision && <p>Recommendation: {prompt.decision} {prompt.selected_choice ? `(${prompt.selected_choice})` : ''} · {prompt.decision_source ?? 'unknown source'}</p>}
                {prompt.review_notification_id && <p>Review progress: {props.controllers.filter((item) => item.current_notification_id === prompt.review_notification_id || prompt.reviewer_ids.includes(item.controller_id)).map((item) => `${item.controller_id}: ${item.status}`).join(' · ') || 'queued'}</p>}
                {prompt.error && <p className="error-copy">{prompt.error}</p>}
                <div className="prompt-actions">
                  {prompt.choices.map((choice) => (
                    <button disabled={busy !== null} key={choice.choice} onClick={() => void run(prompt.prompt_id, () => props.onDecision(prompt, choice.choice))}>{choice.label}</button>
                  ))}
                  <button disabled={busy !== null} onClick={() => void run(prompt.prompt_id, () => props.onReview(prompt, 'fast'))}>Fast review</button>
                  <button disabled={busy !== null} onClick={() => void run(prompt.prompt_id, () => props.onReview(prompt, 'council'))}>Council</button>
                  <button disabled={busy !== null || props.automation.disabled_hosts.includes(prompt.host)} onClick={() => void run(`host-${prompt.host}`, () => props.onAutomation({ disabled_hosts: [...props.automation.disabled_hosts, prompt.host] }))}>Disable host</button>
                  {prompt.project && <button disabled={busy !== null || props.automation.disabled_projects.includes(prompt.project)} onClick={() => void run(`project-${prompt.project}`, () => props.onAutomation({ disabled_projects: [...props.automation.disabled_projects, prompt.project!] }))}>Disable project</button>}
                  <button disabled={busy !== null || props.automation.disabled_sessions.includes(prompt.session_id)} onClick={() => void run(`session-${prompt.session_id}`, () => props.onAutomation({ disabled_sessions: [...props.automation.disabled_sessions, prompt.session_id] }))}>Disable session</button>
                  <button disabled={busy !== null || props.automation.disabled_workers.includes(prompt.worker_id)} onClick={() => void run(`worker-${prompt.worker_id}`, () => props.onAutomation({ disabled_workers: [...props.automation.disabled_workers, prompt.worker_id] }))}>Disable worker</button>
                  {prompt.normalized_argv.length > 0 && prompt.choices.some((item) => item.choice === 'allow') && (
                    <button disabled={busy !== null} onClick={() => void run(`policy-${prompt.prompt_id}`, () => props.onCreatePolicy(prompt, 'allow'))}>Trust exact command</button>
                  )}
                </div>
              </article>
            ))}
          </div>
          <div className="policy-list">
            {(props.automation.disabled_hosts.length + props.automation.disabled_projects.length + props.automation.disabled_sessions.length + props.automation.disabled_workers.length) > 0 && <>
              <h3>Disabled scopes</h3>
              {props.automation.disabled_hosts.map((value) => <button key={`host-${value}`} onClick={() => void run(`enable-host-${value}`, () => props.onAutomation({ disabled_hosts: props.automation.disabled_hosts.filter((item) => item !== value) }))}>Enable host {value}</button>)}
              {props.automation.disabled_projects.map((value) => <button key={`project-${value}`} onClick={() => void run(`enable-project-${value}`, () => props.onAutomation({ disabled_projects: props.automation.disabled_projects.filter((item) => item !== value) }))}>Enable project {value}</button>)}
              {props.automation.disabled_sessions.map((value) => <button key={`session-${value}`} onClick={() => void run(`enable-session-${value}`, () => props.onAutomation({ disabled_sessions: props.automation.disabled_sessions.filter((item) => item !== value) }))}>Enable session {value}</button>)}
              {props.automation.disabled_workers.map((value) => <button key={`worker-${value}`} onClick={() => void run(`enable-worker-${value}`, () => props.onAutomation({ disabled_workers: props.automation.disabled_workers.filter((item) => item !== value) }))}>Enable worker {value}</button>)}
            </>}
            <h3>Policies <span>{props.policies.filter((item) => item.status === 'active').length}</span></h3>
            {props.policies.map((policy) => (
              <article key={policy.policy_id}>
                <div><strong>{policy.name}</strong><span>{policy.status}</span></div>
                <code>{policy.command_argv.join(' ')}</code>
                <p>{policy.match_kind} · {policy.host ?? 'any host'} · {policy.project ?? 'any project'} · used {policy.usage_count} · {policy.provenance}</p>
                {policy.status === 'active' && <button onClick={() => void run(policy.policy_id, () => props.onRevokePolicy(policy))}>Revoke</button>}
                {(policy.status === 'candidate' || policy.status === 'suspended') && <button onClick={() => void run(policy.policy_id, () => props.onActivatePolicy(policy))}>Activate</button>}
              </article>
            ))}
          </div>
        </div>
        </>}
        {view === 'audit' && <div className="council-audit">
          <header>
            <div><h3>Council decision trail</h3><p>Typed votes, rationale, final disposition, and verified pane outcome.</p></div>
            <span>{councilAudit.length} records</span>
          </header>
          {councilAudit.length === 0 && <p className="empty-copy">No council prompt reviews have been recorded.</p>}
          {councilAudit.map((prompt) => {
            const reviewers = Array.from(new Set([
              ...prompt.reviewer_ids,
              ...Object.keys(prompt.review_decisions ?? {}),
              ...Object.keys(prompt.review_rationales ?? {}),
            ]))
            return <article className="audit-record" key={prompt.prompt_id}>
              <div className="audit-summary">
                <span className={`audit-outcome outcome-${prompt.status}`}>{prompt.status}</span>
                <div>
                  <strong>{prompt.decision ?? 'No final decision'}{prompt.selected_choice ? ` · ${prompt.selected_choice}` : ''}</strong>
                  <code>{prompt.operation}</code>
                </div>
                <time dateTime={prompt.updated_at}>{timestamp(prompt.completed_at ?? prompt.updated_at)}</time>
              </div>
              <dl className="audit-context">
                <div><dt>Worker</dt><dd>{prompt.host}:{prompt.window_name}.{prompt.pane_index}</dd></div>
                <div><dt>Project</dt><dd>{prompt.project ?? 'unknown'}</dd></div>
                <div><dt>Review</dt><dd>{prompt.tier} · {prompt.review_notification_id?.slice(0, 8) ?? 'no notification'}</dd></div>
                <div><dt>Execution</dt><dd>{prompt.executed_at ? timestamp(prompt.executed_at) : 'not executed'}</dd></div>
              </dl>
              <div className="audit-votes">
                <h4>Reviewer decisions</h4>
                {reviewers.length === 0 && <p className="empty-copy">No typed reviewer decisions were recorded.</p>}
                {reviewers.map((reviewer) => <div className="audit-vote" key={reviewer}>
                  <strong>{reviewer}</strong>
                  <span>{prompt.review_decisions?.[reviewer] ?? 'missing'}{prompt.review_choices?.[reviewer] ? ` · ${prompt.review_choices[reviewer]}` : ''}</span>
                  <p>{prompt.review_rationales?.[reviewer] ?? 'No rationale recorded.'}</p>
                </div>)}
              </div>
              {prompt.rationale && <p className="audit-final"><strong>Final rationale</strong>{prompt.rationale}</p>}
              {prompt.error && <p className="audit-error"><strong>Outcome detail</strong>{prompt.error}</p>}
              <footer>
                <span>Prompt {prompt.prompt_id.slice(0, 8)}</span>
                <span>Evidence {prompt.observation_fingerprint.slice(0, 8)}</span>
                <span>Pane {prompt.pre_action_fingerprint?.slice(0, 8) ?? '—'} → {prompt.post_action_fingerprint?.slice(0, 8) ?? '—'}</span>
              </footer>
            </article>
          })}
        </div>}
      </section>
    </div>
  )
}
