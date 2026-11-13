export interface Me {
  user_id: string
  name: string
  username: string
  is_admin: boolean
  max_accounts: number
}

export interface MarketStatus {
  stocks: number
  last_trade_date: string | null
  updated_at: string | null
}

export type ChatEvent = { type: string; [k: string]: unknown }

export type ConversationEvent = { type: 'conversation'; id: string }
export type RoutingEvent = {
  type: 'routing'
  difficulty: string
  profile: string
  tier: string
  model: string
  reason: string
}
export type ModelFallbackEvent = {
  type: 'model_fallback'
  from_model: string
  to_model?: string
  category: string
  agent?: string
}
export type AgentEvent = { type: 'agent'; name: string }
export type StepEvent = { type: 'step'; agent: string; name: string }
export type FallbackEvent = { type: 'fallback' }
export type TokenEvent = { type: 'token'; text: string; agent?: string; task?: string }
export type ToolCallEvent = {
  type: 'tool_call'
  name: string
  args: Record<string, unknown>
  agent?: string
  task?: string
}
export type ToolResultEvent = {
  type: 'tool_result'
  name: string
  content: string
  charts?: unknown[]
  agent?: string
  task?: string
}
export type UsageEvent = {
  type: 'usage'
  reply: unknown
  chat: unknown
  reply_by_model?: unknown
  chat_by_model?: unknown
}
export type ErrorEvent = { type: 'error'; message: string; code?: string }
export type DoneEvent = { type: 'done' }

export type ChartSpec = {
  id: string
  type: 'line' | 'bar' | 'area' | 'pie' | 'scatter'
  title: string
  x_label: string
  y_label: string
  labels: (string | number)[]
  series: { name: string; values: (number | null)[] }[]
  downsampled_from?: number | null
  table_id?: string | null
}

export type UsagePart = { input_tokens: number; output_tokens: number; cached_tokens: number | null }
export type ModelUsage = UsagePart & { model_calls: number }
export type Usage = {
  reply: UsagePart | null
  chat: UsagePart
  reply_by_model?: Record<string, ModelUsage>
  chat_by_model?: Record<string, ModelUsage>
}

export type Conversation = { id: string; title: string; kind: 'auto' | 'team' }
