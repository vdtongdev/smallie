import { describe, expect, it } from 'vitest'
import { turnsFromHistory, type HistoryMessage } from './history'
import { endTurn, newTurn, reduceTurn, type Answer, type Tool, type Turn } from './turn'
import type { ChatEvent } from './types'

type Fixture = { name: string; message: string; events: ChatEvent[]; history: HistoryMessage[]; usage: unknown }

const files = import.meta.glob<Fixture>('./__fixtures__/*.json', { eager: true, import: 'default' })
const fixtures = Object.entries(files).map(([path, f]) => [path.replace(/^.*\/|\.json$/g, ''), f] as const)

const names = (tools: Tool[]) => tools.map((x) => x.name)
const answerView = (a: Answer) => ({ agent: a.agent, task: a.task, text: a.text, tools: names(a.tools) })

const view = (t: Turn) => ({
  plan: t.activity.plan,
  waves: t.activity.waves.map((w) => ({ wave: w.wave, kind: w.kind, tasks: w.tasks, answers: w.answers.map(answerView) })),
  drawn: t.activity.drawn.map(answerView),
  pending: t.pending,
  badges: t.badges,
  routing: t.activity.routing,
  fallbacks: t.activity.fallbacks,
  asked: t.activity.asked.map((a) => ({
    agent: a.agent,
    tools: a.tools.map((x) => ({ name: x.name, result: x.result?.slice(0, 4000) ?? null })),
  })),
  agents: t.badges.filter((b) => !b.step).map((b) => b.agent),
  text: t.blocks
    .flatMap((b) => (b.kind === 'text' ? [b.text.trim()] : []))
    .filter(Boolean)
    .join('\n'),
  chartIds: t.blocks.flatMap((b) => (b.kind === 'charts' ? b.charts.map((c) => c.id) : [])),
  tools: t.activity.tools.map((x) => ({ name: x.name, result: x.result?.slice(0, 4000) ?? null })),
  streamCut: t.blocks.some((b) => b.kind === 'error' && b.code === 'stream_cut'),
})

describe('replay of recorded chats', () => {
  it('has all seven recordings of 1-to-1 chats', () => {
    expect(fixtures.map(([n]) => n).filter((n) => !n.startsWith('team_')).sort()).toEqual(['asked', 'chart', 'fallback', 'general', 'research', 'sql', 'vi'])
  })

  it.each(fixtures)('%s: the stream gives the turn its history gives', (name, f) => {
    // A `user_reply` marker (fixture-only) starts the run that answers a paused team's question: the reply is
    // the next user message, so each run is one turn, and the paused one is no longer pending.
    const runs: { user: string; events: ChatEvent[] }[] = [{ user: f.message, events: [] }]
    for (const e of f.events) {
      if (e.type === 'user_reply') runs.push({ user: e.text as string, events: [] })
      else runs.at(-1)!.events.push(e)
    }
    const turns = turnsFromHistory(f.history)
    expect(turns).toHaveLength(runs.length)
    runs.forEach((run, i) => {
      const live = endTurn(run.events.reduce(reduceTurn, newTurn(run.user, name.startsWith('team_'))))
      expect(turns[i].user).toBe(run.user)
      const answered = i < runs.length - 1 // paused live, but its reply is in the history: no longer pending
      if (answered) expect(live.pending).toBe(true)
      expect(view(live)).toEqual({ ...view(turns[i]), ...(answered ? { pending: true } : {}) })
      expect(view(live).streamCut).toBe(false)
    })
  })
})
