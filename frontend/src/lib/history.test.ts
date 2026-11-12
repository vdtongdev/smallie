import { describe, expect, it } from 'vitest'
import { turnsFromHistory, type HistoryMessage } from './history'

const msg = (m: Partial<HistoryMessage> & { role: HistoryMessage['role'] }): HistoryMessage => ({
  content: '',
  agent: null,
  tool_calls: [],
  charts: [],
  team: null,
  ...m,
})
const chart = { id: 'c1', type: 'line' as const, title: 'T', labels: ['a'], series: [{ name: 's', values: [1] }] }

describe('turnsFromHistory', () => {
  it('builds a finished turn per user message', () => {
    const turns = turnsFromHistory([
      msg({ role: 'user', content: 'q1' }),
      msg({ role: 'assistant', agent: 'sql', content: 'a1' }),
      msg({ role: 'user', content: 'q2' }),
      msg({ role: 'assistant', agent: 'docs', content: 'a2' }),
    ])
    expect(turns.map((t) => t.user)).toEqual(['q1', 'q2'])
    expect(turns.map((t) => t.badges)).toEqual([[{ agent: 'sql' }], [{ agent: 'docs' }]])
    expect(turns.every((t) => !t.running && t.finished)).toBe(true)
    expect(turns[0].blocks).toEqual([{ kind: 'text', text: 'a1' }])
  })

  it('adds a badge only when the agent changes', () => {
    const [t] = turnsFromHistory([
      msg({ role: 'user', content: 'q' }),
      msg({ role: 'assistant', agent: 'sql', tool_calls: [{ name: 'run_sql', args: {} }] }),
      msg({ role: 'tool', name: 'run_sql', content: 'rows' }),
      msg({ role: 'assistant', agent: 'sql', content: 'answer' }),
      msg({ role: 'assistant', agent: 'orchestrator', content: 'more' }),
    ])
    expect(t.badges).toEqual([{ agent: 'sql' }, { agent: 'orchestrator' }])
  })

  it('tool calls become tools closed by the tool message, whose charts follow in order', () => {
    const [t] = turnsFromHistory([
      msg({ role: 'user', content: 'q' }),
      msg({ role: 'assistant', agent: 'chart', tool_calls: [{ name: 'chart_sql', args: { query: 'x' } }] }),
      msg({ role: 'tool', name: 'chart_sql', content: 'drawn', charts: [chart] }),
      msg({ role: 'assistant', agent: 'chart', content: 'done' }),
    ])
    expect(t.activity.tools).toEqual([{ name: 'chart_sql', args: { query: 'x' }, result: 'drawn' }])
    expect(t.blocks).toEqual([
      { kind: 'charts', charts: [chart] },
      { kind: 'text', text: 'done' },
    ])
  })

  it('an assistant message puts its charts before its text', () => {
    const [t] = turnsFromHistory([
      msg({ role: 'user', content: 'q' }),
      msg({ role: 'assistant', agent: 'orchestrator', content: 'here', charts: [chart] }),
    ])
    expect(t.blocks).toEqual([
      { kind: 'charts', charts: [chart] },
      { kind: 'text', text: 'here' },
    ])
  })

  it('a tool message without a call adds one', () => {
    const [t] = turnsFromHistory([msg({ role: 'user', content: 'q' }), msg({ role: 'tool', name: 'x', content: 'r' })])
    expect(t.activity.tools).toEqual([{ name: 'x', args: {}, result: 'r' }])
  })

  it('a pending message sets pending and the question', () => {
    const [t] = turnsFromHistory([
      msg({ role: 'user', content: 'q' }),
      msg({ role: 'assistant', agent: 'orchestrator', content: 'Which one?', pending: true }),
    ])
    expect(t.pending).toBe(true)
    expect(t.activity.question).toBe('Which one?')
    expect(t.blocks).toEqual([{ kind: 'text', text: 'Which one?' }])
  })

  it('a team message renders its text and charts only', () => {
    const [t] = turnsFromHistory([
      msg({ role: 'user', content: 'q' }),
      msg({ role: 'assistant', agent: 'orchestrator', content: 'team answer', team: { tasks: [{ agent: 'sql' }] } }),
    ])
    expect(t.blocks).toEqual([{ kind: 'text', text: 'team answer' }])
    expect(t.badges).toEqual([{ agent: 'orchestrator' }])
  })

  it('uses saved events for badges, steps and activity', () => {
    const routing = { type: 'routing', difficulty: 'easy', profile: 'p', tier: 't', model: 'm', reason: 'r' }
    const [t] = turnsFromHistory([
      msg({
        role: 'user',
        content: 'q',
        activity: [
          routing,
          { type: 'agent', name: 'research' },
          { type: 'step', agent: 'research', name: 'write' },
          { type: 'tool_call', name: 'tavily_search', args: { q: 'x' } },
          { type: 'tool_result', name: 'tavily_search', content: 'r' },
        ],
      }),
      msg({ role: 'assistant', agent: 'orchestrator', content: 'a' }),
    ])
    expect(t.badges).toEqual([{ agent: 'research' }, { agent: 'research', step: 'write' }])
    expect(t.activity.routing).toEqual(routing)
    expect(t.activity.tools).toEqual([{ name: 'tavily_search', args: { q: 'x' }, result: 'r' }])
    expect(t.blocks).toEqual([{ kind: 'text', text: 'a' }])
  })

  it('asked agents keep their tools on reload', () => {
    const [t] = turnsFromHistory([
      msg({
        role: 'user',
        content: 'q',
        activity: [
          { type: 'tool_call', name: 'run_sql', args: {}, agent: 'sql' },
          { type: 'tool_result', name: 'run_sql', content: 'rows', agent: 'sql' },
        ],
      }),
      msg({ role: 'assistant', agent: 'orchestrator', content: 'a' }),
    ])
    expect(t.activity.asked).toHaveLength(1)
    expect(t.activity.asked[0].agent).toBe('sql')
    expect(t.activity.asked[0].tools).toEqual([{ name: 'run_sql', args: {}, result: 'rows' }])
    expect(t.activity.tools).toEqual([])
  })

  it('an agent after text adds a badge on reload', () => {
    const [t] = turnsFromHistory([
      msg({
        role: 'user',
        content: 'q',
        activity: [
          { type: 'agent', name: 'orchestrator' },
          { type: 'token', text: '' },
          { type: 'agent', name: 'sql' },
        ],
      }),
      msg({ role: 'assistant', agent: 'sql', content: 'a' }),
    ])
    expect(t.badges).toEqual([{ agent: 'orchestrator' }, { agent: 'sql' }])
  })

  it('returns no turns for no messages', () => {
    expect(turnsFromHistory([])).toEqual([])
  })
})
