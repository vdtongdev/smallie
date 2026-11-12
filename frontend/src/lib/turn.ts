import type { ChatEvent, ModelFallbackEvent, RoutingEvent } from './types'

export type ChartSpec = {
  id: string
  type: 'line' | 'area' | 'bar' | 'pie' | 'scatter'
  title: string
  labels: string[]
  series: { name: string; values: (number | null)[] }[]
  x_label?: string
  y_label?: string
  [k: string]: unknown
}

export type Tool = {
  name: string
  args: Record<string, unknown>
  result: string | null
  charts?: ChartSpec[]
}
export type Badge = { agent: string; step?: string }
export type Block =
  | { kind: 'text'; text: string }
  | { kind: 'charts'; charts: ChartSpec[] }
  | { kind: 'error'; message: string; code?: string }
export type Activity = {
  routing: RoutingEvent | null
  fallbacks: ModelFallbackEvent[]
  tools: Tool[]
  asked: { agent: string; text: string; tools: Tool[] }[]
  question: string | null
}
export type Turn = {
  user: string
  running: boolean
  badges: Badge[]
  step: { tool?: string }
  blocks: Block[]
  activity: Activity
  pending: boolean
  finished: boolean
  waiting: boolean
  started: boolean
}

export const newTurn = (user: string): Turn => ({
  user,
  running: true,
  badges: [],
  step: {},
  blocks: [],
  activity: { routing: null, fallbacks: [], tools: [], asked: [], question: null },
  pending: false,
  finished: false,
  waiting: false,
  started: false,
})

/** A tool result closes the oldest call of that name still waiting for one (adds the call if there was none). */
export function closeTool(tools: Tool[], name: string, content: string): Tool {
  let tool = tools.find((x) => x.name === name && x.result === null)
  if (!tool) {
    tool = { name, args: {}, result: null }
    tools.push(tool)
  }
  tool.result = content
  return tool
}

const askedEntry = (t: Turn, agent: string) => {
  let entry = t.activity.asked.find((a) => a.agent === agent)
  if (!entry) {
    entry = { agent, text: '', tools: [] }
    t.activity.asked.push(entry)
  }
  return entry
}

/** Pure: returns a new turn. Team-only events (anything carrying a `task`) and unknown types change nothing. */
export function reduceTurn(turn: Turn, event: ChatEvent): Turn {
  const e = event as Record<string, any> // eslint-disable-line @typescript-eslint/no-explicit-any
  if (e.task !== undefined) return turn
  const t = structuredClone(turn)
  switch (event.type) {
    case 'routing':
      t.activity.routing = event as unknown as RoutingEvent
      return t
    case 'model_fallback':
      t.activity.fallbacks.push(event as unknown as ModelFallbackEvent)
      return t
    case 'agent':
      if (e.name === 'team') return turn
      if (t.badges.length && !t.started) t.badges[t.badges.length - 1] = { agent: e.name }
      else t.badges.push({ agent: e.name })
      return t
    case 'step': {
      const last = t.badges.at(-1)
      if (last && last.agent === e.agent && last.step === e.name) return turn
      t.badges.push({ agent: e.agent, step: e.name })
      return t
    }
    case 'fallback':
      t.blocks = t.blocks.filter((b) => b.kind !== 'text')
      t.waiting = true
      t.started = true
      return t
    case 'token': {
      if (e.agent) {
        askedEntry(t, e.agent).text += e.text
        return t
      }
      const last = t.blocks.at(-1)
      if (last?.kind === 'text' && !t.waiting) last.text += e.text
      else t.blocks.push({ kind: 'text', text: e.text })
      t.started = true
      t.waiting = false
      return t
    }
    case 'tool_call': {
      const tool: Tool = { name: e.name, args: e.args ?? {}, result: null }
      if (e.agent) {
        askedEntry(t, e.agent).tools.push(tool)
        return t
      }
      t.activity.tools.push(tool)
      t.step = { tool: e.name }
      // Close the text so far: the next token starts a new block (an empty one stays until endTurn).
      const last = t.blocks.at(-1)
      if (last?.kind === 'text' && last.text) t.blocks.push({ kind: 'text', text: '' })
      t.started = true
      t.waiting = false
      return t
    }
    case 'tool_result': {
      const charts: ChartSpec[] | undefined = e.charts
      if (e.agent) {
        closeTool(askedEntry(t, e.agent).tools, e.name, e.content).charts = charts
        return t
      }
      closeTool(t.activity.tools, e.name, e.content)
      t.step = {}
      if (charts?.length) t.blocks.push({ kind: 'charts', charts })
      return t
    }
    case 'error':
      t.blocks.push({ kind: 'error', message: e.message, ...(e.code ? { code: e.code } : {}) })
      return t
    case 'done':
      t.finished = true
      return t
    default:
      return turn
  }
}

/** The reply is over (normally, or because `error` ended it): no spinner, no empty text, a cut stream is marked. */
export function endTurn(turn: Turn, error?: string): Turn {
  const t = structuredClone(turn)
  t.running = false
  t.step = {}
  t.blocks = t.blocks.filter((b) => b.kind !== 'text' || b.text)
  if (error) t.blocks.push({ kind: 'error', message: error })
  else if (!t.finished) t.blocks.push({ kind: 'error', message: '', code: 'stream_cut' })
  return t
}
