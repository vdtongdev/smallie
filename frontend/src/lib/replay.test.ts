import { describe, expect, it } from 'vitest'
import { turnsFromHistory, type HistoryMessage } from './history'
import { endTurn, newTurn, reduceTurn, type Turn } from './turn'
import type { ChatEvent } from './types'

type Fixture = { name: string; message: string; events: ChatEvent[]; history: HistoryMessage[]; usage: unknown }

const files = import.meta.glob<Fixture>('./__fixtures__/*.json', { eager: true, import: 'default' })
const fixtures = Object.entries(files).map(([path, f]) => [path.replace(/^.*\/|\.json$/g, ''), f] as const)

const view = (t: Turn) => ({
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
  it('has all seven recordings', () => {
    expect(fixtures.map(([n]) => n).sort()).toEqual(['asked', 'chart', 'fallback', 'general', 'research', 'sql', 'vi'])
  })

  it.each(fixtures)('%s: the stream gives the turn its history gives', (_name, f) => {
    const live = endTurn(f.events.reduce(reduceTurn, newTurn(f.message)))
    const turns = turnsFromHistory(f.history)
    expect(turns).toHaveLength(1)
    const [saved] = turns
    expect(saved.user).toBe(f.message)
    expect(view(live)).toEqual(view(saved))
    expect(view(live).streamCut).toBe(false)
  })
})
