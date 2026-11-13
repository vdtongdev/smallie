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

  it('an older team chat (no saved activity) builds plan, waves and badges from message.team', () => {
    const team = {
      tasks: [
        { id: 't1', agent: 'sql', goal: 'g', depends_on: [], origin: 'plan' },
        { id: 't2', agent: 'docs', goal: 'h', depends_on: [], origin: 'plan' },
      ],
      history: [
        { type: 'wave', wave: 1, kind: 'plan', tasks: ['t1', 't2'], agents: ['sql', 'docs'] },
        { type: 'result', task: 't1', agent: 'sql', text: 'a', tools: [{ name: 'run_sql', args: {}, result: 'r' }] },
        { type: 'result', task: 't2', agent: 'docs', text: 'b', tools: [] },
        { type: 'review', agreements: [], differences: [] },
      ],
    }
    const [t] = turnsFromHistory([
      msg({ role: 'user', content: 'q' }),
      msg({ role: 'assistant', agent: 'orchestrator', content: 'team answer', team, charts: [chart] }),
    ])
    expect(t.team).toBe(true)
    expect(t.blocks).toEqual([
      { kind: 'charts', charts: [chart] },
      { kind: 'text', text: 'team answer' },
    ])
    expect(t.badges).toEqual([{ agent: 'sql' }, { agent: 'docs' }])
    expect(t.activity.plan.map((x) => x.id)).toEqual(['t1', 't2'])
    expect(t.activity.waves[0].answers.map((a) => a.text)).toEqual(['a', 'b'])
    expect(t.activity.waves[0].review).not.toBeNull()
    expect(t.activity.asked).toEqual([])
  })

  it('an older round-table chat reads rounds and boards; participants name the badges', () => {
    const [t] = turnsFromHistory([
      msg({ role: 'user', content: 'q' }),
      msg({
        role: 'assistant',
        agent: 'orchestrator',
        content: 'a',
        team: { participants: ['docs', 'sql', 'docs'], rounds: [{ round: 1, kind: 'answer', answers: [{ agent: 'docs', text: 'x', tools: [] }] }], boards: [{ round: 1 }] },
      }),
    ])
    expect(t.badges).toEqual([{ agent: 'docs' }, { agent: 'sql' }])
    expect(t.activity.waves).toHaveLength(1)
    expect(t.activity.waves[0].review).toEqual({ round: 1 })
  })

  it('a team turn with saved activity replays its plan, waves and drawn', () => {
    const [t] = turnsFromHistory([
      msg({
        role: 'user',
        content: 'q',
        activity: [
          { type: 'agent', name: 'team' },
          { type: 'plan', tasks: [{ id: 't1', agent: 'sql', goal: 'g', depends_on: [] }] },
          { type: 'wave', wave: 1, kind: 'plan', tasks: ['t1'], agents: ['sql'] },
          { type: 'task_started', task: 't1', agent: 'sql' },
          { type: 'token', text: '', agent: 'sql' },
          { type: 'spoke', task: 't1', agent: 'sql', text: 'rows', tools: [] },
          { type: 'review', agreements: [] },
          { type: 'synthesize' },
          { type: 'token', text: '' },
          { type: 'agent', name: 'team' },
        ],
      }),
      msg({ role: 'assistant', agent: 'orchestrator', content: 'final', team: { tasks: [], history: [] } }),
    ])
    expect(t.team).toBe(true)
    expect(t.synthesized).toBe(true)
    expect(t.badges).toEqual([{ agent: 'sql' }])
    expect(t.activity.plan).toHaveLength(1)
    expect(t.activity.waves[0].answers.map((a) => [a.task, a.text])).toEqual([['t1', 'rows']])
    expect(t.activity.waves[0].review).not.toBeNull()
    expect(t.activity.asked).toEqual([])
    expect(t.blocks).toEqual([{ kind: 'text', text: 'final' }])
  })

  it('a paused team turn reloads pending with the question', () => {
    const [t] = turnsFromHistory([
      msg({ role: 'user', content: 'q', activity: [{ type: 'plan', tasks: [{ id: 't1', agent: 'sql', goal: '', depends_on: [] }] }, { type: 'needs_human', question: 'Age?' }] }),
      msg({ role: 'assistant', agent: 'orchestrator', content: 'Age?', pending: true }),
    ])
    expect(t.team).toBe(true)
    expect(t.pending).toBe(true)
    expect(t.activity.question).toBe('Age?')
    expect(t.blocks).toEqual([{ kind: 'text', text: 'Age?' }])
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

  it('text saved before a tool call comes back ahead of the answer', () => {
    const [t] = turnsFromHistory([
      msg({
        role: 'user',
        content: 'q',
        activity: [
          { type: 'agent', name: 'orchestrator' },
          { type: 'token', text: 'Let me check.' },
          { type: 'tool_call', name: 'x', args: {} },
          { type: 'tool_result', name: 'x', content: 'r' },
          { type: 'token', text: '' },
        ],
      }),
      msg({ role: 'assistant', agent: 'orchestrator', content: 'The answer.' }),
    ])
    expect(t.blocks).toEqual([
      { kind: 'text', text: 'Let me check.' },
      { kind: 'text', text: 'The answer.' },
    ])
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
