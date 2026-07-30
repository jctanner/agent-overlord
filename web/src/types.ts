export type WorkerState =
  | 'active'
  | 'awaiting_input'
  | 'idle'
  | 'stalled'
  | 'failed'
  | 'complete'
  | 'disconnected'
  | 'unknown'

export interface Observation {
  host: string
  tmux_socket: string
  session_id: string
  session_name: string
  window_id: string
  window_name: string
  pane_id: string
  pane_index: number
  pane_title: string
  current_path: string
  current_command: string
  start_command: string
  descendant_commands: string[]
  content: string[]
  observed_at: string
  display_name: string
  content_fingerprint: string
}

export interface Worker {
  worker_id: string
  observation: Observation
  harness: string
  model: string
  context: string
  purpose: string
  project: string | null
  state: WorkerState
  awaiting_input: boolean
  input_kind: string | null
  confidence: number
  evidence: string[]
  first_seen_at: string
  last_seen_at: string
  unchanged_since: string
}

export interface WallEvent {
  event_id: string
  created_at: string
  actor: string
  kind: string
  message: string
  worker_id: string | null
  host: string | null
  intent: string | null
  severity: string
  data: Record<string, unknown>
}

export interface ChatMessage {
  role: string
  message: string
}

export interface Memory {
  memory_id: string
  scope: string
  kind: string
  claim: string
  source: string
  created_by: string
  confidence: number
  status: 'candidate' | 'active' | 'superseded' | 'stale'
  created_at: string
  updated_at: string
}

export interface HostHealth {
  name: string
  connected: boolean
  error: string | null
  worker_count: number
}

export interface Health {
  status: string
  inventory_running: boolean
  started_at: string
  configured_hosts: number
  workers: number
  stream_clients: number
  hosts: HostHealth[]
}

export interface IgnoredSession {
  ignore_id: string
  host: string
  tmux_socket: string
  session_id: string
  session_name: string
  created_at: string
}

export type CouncilNotificationStatus =
  | 'pending' | 'running' | 'completed' | 'failed' | 'timed_out' | 'superseded'

export interface CouncilNotification {
  notification_id: string
  reason: string
  priority: number
  target_roles: string[]
  worker_id: string | null
  human_message: string | null
  status: CouncilNotificationStatus
  attempts: number
  summary: string | null
  answer: string | null
  answered_by: string | null
  answer_published_at?: string | null
  error: string | null
  created_at: string
}

export interface ControllerState {
  controller_id: string
  role: string
  harness: string
  model: string
  status: 'stopped' | 'starting' | 'ready' | 'busy' | 'failed' | 'restarting'
  current_notification_id: string | null
  cycles_completed: number
  restart_count: number
  last_error: string | null
}

export interface Snapshot {
  workers: Worker[]
  events: WallEvent[]
  messages: ChatMessage[]
  memories: Memory[]
  health: Health
  controllers: ControllerState[]
  notifications: CouncilNotification[]
  ignored_sessions: IgnoredSession[]
  prompts: PromptRequest[]
  policies: ApprovalPolicy[]
  automation: AutomationSettings
}

export type PromptStatus =
  | 'detected' | 'evaluating' | 'escalated' | 'decided' | 'executing'
  | 'succeeded' | 'rejected' | 'stale' | 'failed' | 'expired'
export type PromptRisk = 'routine' | 'elevated' | 'high' | 'unknown'
export type PromptDecision = 'allow' | 'deny' | 'escalate'
export type ReviewTier = 'policy' | 'fast' | 'council' | 'human'

export interface PromptChoice {
  choice: string
  label: string
  response: string
}

export interface PromptRequest {
  prompt_id: string
  worker_id: string
  host: string
  tmux_socket: string
  session_id: string
  session_name: string
  window_id: string
  window_name: string
  pane_id: string
  pane_index: number
  harness: string
  project: string | null
  prompt_type: string
  operation: string
  normalized_argv: string[]
  choices: PromptChoice[]
  observation_fingerprint: string
  prompt_signature: string
  evidence: string[]
  confidence: number
  risk: PromptRisk
  risk_reasons: string[]
  tier: ReviewTier
  status: PromptStatus
  decision: PromptDecision | null
  selected_choice: string | null
  decision_source: string | null
  policy_id: string | null
  reviewer_ids: string[]
  review_decisions?: Record<string, PromptDecision>
  review_choices?: Record<string, string>
  review_rationales?: Record<string, string>
  review_notification_id?: string | null
  rationale: string | null
  pre_action_fingerprint?: string | null
  post_action_fingerprint?: string | null
  executed_at?: string | null
  error: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface ApprovalPolicy {
  policy_id: string
  name: string
  status: 'candidate' | 'active' | 'suspended' | 'revoked' | 'expired'
  decision: PromptDecision
  match_kind: 'exact' | 'argv_prefix'
  command_argv: string[]
  allowed_choices: string[]
  harness: string | null
  host: string | null
  project: string | null
  worker_id: string | null
  session_id: string | null
  risk_ceiling: PromptRisk
  provenance: string
  usage_count: number
  failure_count: number
  last_used_at?: string | null
  confirmed_at?: string | null
}

export interface AutomationSettings {
  automation_enabled: boolean
  dry_run: boolean
  paused: boolean
  disabled_hosts: string[]
  disabled_projects: string[]
  disabled_sessions: string[]
  disabled_workers: string[]
  auto_yes_workers: string[]
  prompt_expiration_secs: number
  verification_timeout_secs: number
  max_actions_per_pane_per_hour: number
  auto_yes_max_actions_per_worker_per_hour: number
  review_precedent_ttl_secs: number
  updated_at: string
}
