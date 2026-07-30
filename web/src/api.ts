import type { ApprovalPolicy, AutomationSettings, ChatMessage, Memory, PromptDecision, PromptRequest, Snapshot, Worker } from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: init?.body
      ? { 'Content-Type': 'application/json', ...init.headers }
      : init?.headers,
  })
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `${response.status} ${response.statusText}`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  snapshot: () => request<Snapshot>('/api/snapshot'),
  worker: (id: string) => request<Worker>(`/api/workers/${id}`),
  forgetWorker: (id: string) =>
    request<void>(`/api/workers/${id}`, { method: 'DELETE' }),
  ignoreWorkerSession: (id: string) =>
    request<{ removed_worker_ids: string[] }>(`/api/workers/${id}/ignore-session`, {
      method: 'POST',
    }),
  restoreIgnoredSessions: () =>
    request<{ restored_ignore_ids: string[]; workers: Worker[] }>('/api/ignored-sessions/restore-all', {
      method: 'POST',
    }),
  decidePrompt: (
    id: string, decision: PromptDecision, choice: string | null,
    expectedFingerprint: string, expectedWorkerId: string, expectedPaneId: string,
    execute: boolean,
  ) => request<PromptRequest>(`/api/prompts/${id}/decision`, {
    method: 'POST',
    body: JSON.stringify({
      decision, choice, expected_fingerprint: expectedFingerprint,
      expected_worker_id: expectedWorkerId, expected_pane_id: expectedPaneId, execute,
    }),
  }),
  reviewPrompt: (id: string, tier: 'fast' | 'council') =>
    request<void>(`/api/prompts/${id}/review`, {
      method: 'POST', body: JSON.stringify({ tier }),
    }),
  createPromptPolicy: (prompt: PromptRequest, choice: string) =>
    request<ApprovalPolicy>('/api/approval-policies', {
      method: 'POST',
      body: JSON.stringify({
        name: `${prompt.project ?? prompt.host}: ${prompt.operation}`,
        decision: choice === 'deny' ? 'deny' : 'allow', match_kind: 'exact',
        command_argv: prompt.normalized_argv, allowed_choices: [choice],
        harness: prompt.harness, host: prompt.host, project: prompt.project,
        risk_ceiling: prompt.risk, provenance: `Created from prompt ${prompt.prompt_id}`,
      }),
    }),
  revokePolicy: (id: string) =>
    request<ApprovalPolicy>(`/api/approval-policies/${id}`, { method: 'DELETE' }),
  activatePolicy: (id: string) =>
    request<ApprovalPolicy>(`/api/approval-policies/${id}/activate`, { method: 'POST' }),
  updateAutomation: (patch: Partial<AutomationSettings>) =>
    request<AutomationSettings>('/api/automation-settings', {
      method: 'PATCH', body: JSON.stringify(patch),
    }),
  chat: (message: string) =>
    request<{ message: string; worker_ids: string[]; status: string; notification_id: string | null }>('/api/chat', {
      method: 'POST',
      body: JSON.stringify({ message }),
    }),
  createMemory: (claim: string, scope: string) =>
    request<Memory>('/api/memories', {
      method: 'POST',
      body: JSON.stringify({ claim, scope, kind: 'semantic' }),
    }),
  updateMemory: (id: string, claim: string) =>
    request<Memory>(`/api/memories/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ claim }),
    }),
  activateMemory: (id: string) =>
    request<Memory>(`/api/memories/${id}/activate`, { method: 'POST' }),
  deleteMemory: (id: string) =>
    request<void>(`/api/memories/${id}`, { method: 'DELETE' }),
  controllerLogs: (id: string, tail = 200) =>
    request<{ entries: string[] }>(`/api/controllers/${id}/logs?tail=${tail}`),
}

export function appendChat(messages: ChatMessage[], message: ChatMessage): ChatMessage[] {
  const last = messages.at(-1)
  if (last?.role === message.role && last.message === message.message) return messages
  return [...messages, message]
}
