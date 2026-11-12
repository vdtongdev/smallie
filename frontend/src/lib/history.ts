import type { ChatEvent } from './types'
import { closeTool, newTurn, reduceTurn, type Block, type ChartSpec, type Turn } from './turn'

/** One message of `GET /api/conversations/{id}/messages` (`_serialize` in routes_conversations.py). */
export type HistoryMessage = {
  role: 'user' | 'assistant' | 'tool'
  content: string
  agent?: string | null
  name?: string | null
  tool_calls?: { name: string; args: Record<string, unknown> }[]
  charts?: ChartSpec[]
  team?: unknown
  pending?: boolean
  /** On a user message: the events the reply streamed, saved with the turn (null for older chats). */
  activity?: ChatEvent[] | null
}

/** Team chats show their text and charts only (the task graph is M3). */
export function turnsFromHistory(messages: HistoryMessage[]): Turn[] {
  const turns: Turn[] = []
  let turn: Turn | null = null
  let saved = false // this turn's badges and tools come from saved events
  const end = () => {
    if (turn) turns.push({ ...turn, running: false, finished: true })
    turn = null
  }
  const charts = (t: Turn, list?: ChartSpec[]) => {
    if (list?.length) t.blocks.push({ kind: 'charts', charts: list } as Block)
  }
  for (const m of messages) {
    if (m.role === 'user') {
      end()
      saved = Array.isArray(m.activity)
      if (m.activity) {
        const replayed = m.activity.reduce(reduceTurn, newTurn(m.content))
        turn = newTurn(m.content)
        turn.badges = replayed.badges
        turn.activity = replayed.activity
      } else turn = newTurn(m.content)
      turn.started = true
      continue
    }
    if (!turn) {
      turn = newTurn('')
      saved = false
      turn.started = true
    }
    if (m.role === 'assistant') {
      if (!saved && m.agent && turn.badges.at(-1)?.agent !== m.agent) turn.badges.push({ agent: m.agent })
      for (const call of saved ? [] : (m.tool_calls ?? [])) turn.activity.tools.push({ name: call.name, args: call.args, result: null })
      charts(turn, m.charts)
      if (m.content) {
        turn.blocks.push({ kind: 'text', text: m.content })
        if (m.pending) {
          turn.pending = true
          turn.activity.question = m.content
        }
      }
    } else {
      if (!saved) closeTool(turn.activity.tools, m.name ?? '', m.content)
      charts(turn, m.charts)
    }
  }
  end()
  return turns
}
