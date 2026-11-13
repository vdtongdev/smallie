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
export type PlanTask = { id: string; agent: string; goal: string; depends_on: string[]; origin?: string; difficulty?: string }
export type Answer = { task?: string; agent: string; text: string; tools: Tool[]; meta?: Record<string, unknown> }
export type Wave = {
  wave: number
  kind: string
  tasks: string[]
  agents: string[]
  answers: Answer[]
  review: Record<string, any> | null // eslint-disable-line @typescript-eslint/no-explicit-any
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
  asked: Answer[]
  question: string | null
  plan: PlanTask[]
  waves: Wave[]
  drawn: Answer[]
  synthesis: { open_points: unknown[]; not_done: unknown[] } | null
}
export type Turn = {
  user: string
  running: boolean
  badges: Badge[]
  /** `note: ''` marks a review: the UI builds its text from the last wave's review. */
  step: { tool?: string; wave?: Wave; note?: string; writing?: boolean }
  blocks: Block[]
  activity: Activity
  pending: boolean
  finished: boolean
  waiting: boolean
  started: boolean
  team: boolean
  synthesized: boolean
}

export const newTurn = (user: string, team = false): Turn => ({
  user,
  running: true,
  badges: [],
  step: {},
  blocks: [],
  activity: { routing: null, fallbacks: [], tools: [], asked: [], question: null, plan: [], waves: [], drawn: [], synthesis: null },
  pending: false,
  finished: false,
  waiting: false,
  started: false,
  team,
  synthesized: false,
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

/** A task's answer: in the latest wave that ran it, else (a chart task after the write-up) in `drawn`; no task: a specialist the orchestrator asked. */
const answerOf = (t: Turn, agent: string, task?: string): Answer => {
  const wave = task ? t.activity.waves.findLast((w) => w.tasks.includes(task)) : undefined
  const answers = wave ? wave.answers : task ? t.activity.drawn : t.activity.asked
  let answer = answers.find((a) => a.task === task && a.agent === agent)
  if (!answer) {
    answer = { ...(task ? { task } : {}), agent, text: '', tools: [] }
    answers.push(answer)
  }
  return answer
}

const addBadges = (t: Turn, agents: string[]) => {
  for (const agent of agents) if (!t.badges.some((b) => b.agent === agent)) t.badges.push({ agent })
}

/** Pure: returns a new turn. Unknown event types change nothing. */
export function reduceTurn(turn: Turn, event: ChatEvent): Turn {
  const e = event as Record<string, any> // eslint-disable-line @typescript-eslint/no-explicit-any
  const t = structuredClone(turn)
  switch (event.type) {
    case 'routing':
      t.activity.routing = event as unknown as RoutingEvent
      return t
    case 'model_fallback': // a team task's fallback comes with its `spoke` event
      if (t.team) return turn
      t.activity.fallbacks.push(event as unknown as ModelFallbackEvent)
      return t
    case 'agent':
      if (e.name === 'team' || (t.team && t.activity.plan.length)) return turn // a plan names its own agents
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
        if (t.team && !e.task && !e.text) return t // the saved marker of a run of tokens
        answerOf(t, e.agent, e.task).text += e.text
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
        answerOf(t, e.agent, e.task).tools.push(tool)
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
        closeTool(answerOf(t, e.agent, e.task).tools, e.name, e.content).charts = charts
        return t
      }
      closeTool(t.activity.tools, e.name, e.content)
      t.step = {}
      if (charts?.length) t.blocks.push({ kind: 'charts', charts })
      return t
    }
    case 'plan':
      t.activity.plan = e.tasks
      addBadges(t, e.tasks.map((x: PlanTask) => x.agent))
      return t
    case 'plan_updated':
      t.activity.plan.push(...e.tasks)
      addBadges(t, e.tasks.map((x: PlanTask) => x.agent))
      return t
    case 'wave': {
      const wave: Wave = { wave: e.wave, kind: e.kind, tasks: e.tasks, agents: e.agents, answers: [], review: null }
      t.activity.waves.push(wave)
      t.step = { wave }
      addBadges(t, e.agents)
      return t
    }
    case 'review': {
      const wave = t.activity.waves.at(-1)
      if (wave) wave.review = e
      t.step = { note: '' }
      return t
    }
    case 'task_started':
      answerOf(t, e.agent, e.task).meta = e
      return t
    case 'spoke': {
      const mine = answerOf(t, e.agent, e.task)
      const before = mine.tools
      mine.text = e.text
      mine.meta = e
      mine.tools = (e.tools as Tool[]).map((x, i) => {
        const kept = before[i]?.name === x.name ? before[i].charts : undefined
        return kept ? { ...x, charts: kept } : x
      })
      return t
    }
    case 'synthesize':
      t.synthesized = true
      t.step = { writing: true }
      return t
    case 'charts': {
      const shown = new Set(t.blocks.flatMap((b) => (b.kind === 'charts' ? b.charts.map((c) => c.id) : [])))
      const fresh = (e.charts as ChartSpec[]).filter((c) => !shown.has(c.id))
      if (!fresh.length) return t
      const at = t.blocks.findLastIndex((b) => b.kind === 'text')
      t.blocks.splice(at < 0 ? t.blocks.length : at, 0, { kind: 'charts', charts: fresh })
      return t
    }
    case 'needs_human': {
      const last = t.blocks.at(-1)
      if (last?.kind === 'text' && (!last.text || t.waiting)) t.blocks.pop()
      t.blocks.push({ kind: 'text', text: e.question })
      t.activity.question = e.question
      t.pending = true
      t.started = true
      t.waiting = false
      return t
    }
    case 'error':
      t.blocks.push({ kind: 'error', message: e.message, ...(e.code ? { code: e.code } : {}) })
      return t
    case 'done': {
      t.finished = true
      // One participant and no conflict: its round answer is the reply, so no write-up came.
      const only = t.team && !t.started && !t.synthesized ? t.activity.waves[0]?.answers[0] : undefined
      if (only?.text) {
        const shown = new Set(t.blocks.flatMap((b) => (b.kind === 'charts' ? b.charts.map((c) => c.id) : [])))
        const charts = only.tools.flatMap((x) => x.charts ?? []).filter((c) => !shown.has(c.id))
        if (charts.length) t.blocks.push({ kind: 'charts', charts })
        t.blocks.push({ kind: 'text', text: only.text })
        t.started = true
      }
      return t
    }
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
