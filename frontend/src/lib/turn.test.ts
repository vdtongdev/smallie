import { describe, expect, it } from 'vitest'
import type { ChatEvent } from './types'
import { endTurn, newTurn, reduceTurn, type ChartSpec, type Turn } from './turn'

const run = (events: ChatEvent[], turn: Turn = newTurn('hi')): Turn => events.reduce(reduceTurn, turn)
const tok = (text: string, extra: object = {}): ChatEvent => ({ type: 'token', text, ...extra })
const chart = (id: string): ChartSpec => ({
  id,
  type: 'line',
  title: id,
  labels: ['a'],
  series: [{ name: 's', values: [1] }],
})
const texts = (t: Turn) => t.blocks.flatMap((b) => (b.kind === 'text' && b.text ? [b.text] : []))

describe('newTurn', () => {
  it('starts running with nothing in it', () => {
    const t = newTurn('hello')
    expect(t.user).toBe('hello')
    expect(t.running).toBe(true)
    expect(t.finished).toBe(false)
    expect(t.started).toBe(false)
    expect(t.blocks).toEqual([])
    expect(t.badges).toEqual([])
  })
})

describe('reduceTurn', () => {
  it('is pure', () => {
    const t = newTurn('hi')
    const frozen = JSON.stringify(t)
    reduceTurn(t, tok('x'))
    expect(JSON.stringify(t)).toBe(frozen)
  })

  it('accumulates tokens into one text block', () => {
    const t = run([tok('Hel'), tok('lo')])
    expect(t.blocks).toEqual([{ kind: 'text', text: 'Hello' }])
    expect(t.started).toBe(true)
  })

  it('a tool call splits the text and a tool result with charts goes between the texts', () => {
    const c = chart('c1')
    const t = run([
      tok('before'),
      { type: 'tool_call', name: 'chart_sql', args: { query: 'q' } },
      { type: 'tool_result', name: 'chart_sql', content: 'ok', charts: [c] },
      tok('after'),
    ])
    expect(t.blocks.filter((b) => b.kind !== 'text' || b.text)).toEqual([
      { kind: 'text', text: 'before' },
      { kind: 'charts', charts: [c] },
      { kind: 'text', text: 'after' },
    ])
    expect(t.activity.tools).toEqual([{ name: 'chart_sql', args: { query: 'q' }, result: 'ok' }])
  })

  it('text after a tool call without charts starts a new block', () => {
    const t = run([
      tok('a'),
      { type: 'tool_call', name: 'x', args: {} },
      { type: 'tool_result', name: 'x', content: 'r' },
      tok('b'),
    ])
    expect(texts(t)).toEqual(['a', 'b'])
  })

  it('tool_call sets the step and tool_result clears it', () => {
    const called = run([{ type: 'tool_call', name: 'run_sql', args: {} }])
    expect(called.step).toEqual({ tool: 'run_sql' })
    const done = reduceTurn(called, { type: 'tool_result', name: 'run_sql', content: 'r' })
    expect(done.step).toEqual({})
  })

  it('a tool_result closes the oldest open call of that name', () => {
    const t = run([
      { type: 'tool_call', name: 'x', args: { n: 1 } },
      { type: 'tool_call', name: 'x', args: { n: 2 } },
      { type: 'tool_result', name: 'x', content: 'first' },
    ])
    expect(t.activity.tools.map((x) => x.result)).toEqual(['first', null])
  })

  it('a tool_result without a call adds one with empty args', () => {
    const t = run([{ type: 'tool_result', name: 'x', content: 'r' }])
    expect(t.activity.tools).toEqual([{ name: 'x', args: {}, result: 'r' }])
  })

  it('handoff relabel: agent orchestrator then agent sql before any token is one badge', () => {
    const t = run([
      { type: 'agent', name: 'orchestrator' },
      { type: 'agent', name: 'sql' },
    ])
    expect(t.badges).toEqual([{ agent: 'sql' }])
  })

  it('an agent after tokens adds a badge', () => {
    const t = run([{ type: 'agent', name: 'orchestrator' }, tok('x'), { type: 'agent', name: 'sql' }])
    expect(t.badges).toEqual([{ agent: 'orchestrator' }, { agent: 'sql' }])
  })

  it('ignores the team agent', () => {
    expect(run([{ type: 'agent', name: 'team' }]).badges).toEqual([])
  })

  it('adds a step badge but not a repeated one', () => {
    const t = run([
      { type: 'agent', name: 'docs' },
      { type: 'step', agent: 'docs', name: 'search' },
      { type: 'step', agent: 'docs', name: 'search' },
      { type: 'step', agent: 'docs', name: 'answer' },
    ])
    expect(t.badges).toEqual([
      { agent: 'docs' },
      { agent: 'docs', step: 'search' },
      { agent: 'docs', step: 'answer' },
    ])
  })

  it('fallback drops the text, shows thinking again and the next tokens start fresh', () => {
    const c = chart('c1')
    const t = run([
      tok('draft'),
      { type: 'tool_call', name: 'x', args: {} },
      { type: 'tool_result', name: 'x', content: 'r', charts: [c] },
      tok('more draft'),
      { type: 'fallback' },
    ])
    expect(texts(t)).toEqual([])
    expect(t.waiting).toBe(true)
    expect(t.started).toBe(true)
    expect(t.activity.tools).toHaveLength(1)
    const next = reduceTurn(t, tok('real'))
    expect(texts(next)).toEqual(['real'])
    expect(next.waiting).toBe(false)
  })

  it('after a fallback the assistant agent event adds its own badge', () => {
    const t = run([{ type: 'agent', name: 'docs' }, tok('d'), { type: 'fallback' }, { type: 'agent', name: 'orchestrator' }])
    expect(t.badges).toEqual([{ agent: 'docs' }, { agent: 'orchestrator' }])
  })

  it('asked agent tokens, tools and charts stay off the blocks', () => {
    const c = chart('c9')
    const t = run([
      { type: 'tool_call', name: 'ask_sql', args: {} },
      { type: 'tool_call', name: 'run_sql', args: { query: 'q' }, agent: 'sql' },
      { type: 'tool_result', name: 'run_sql', content: 'rows', agent: 'sql', charts: [c] },
      tok('draft ', { agent: 'sql' }),
      tok('text', { agent: 'sql' }),
    ])
    expect(t.activity.asked).toEqual([
      { agent: 'sql', text: 'draft text', tools: [{ name: 'run_sql', args: { query: 'q' }, result: 'rows', charts: [c] }] },
    ])
    expect(t.activity.tools.map((x) => x.name)).toEqual(['ask_sql'])
    expect(t.blocks.some((b) => b.kind === 'charts')).toBe(false)
    expect(texts(t)).toEqual([])
  })

  it('an asked tool_result without a call adds one under the agent', () => {
    const t = run([{ type: 'tool_result', name: 'x', content: 'r', agent: 'sql' }])
    expect(t.activity.asked[0].tools).toEqual([{ name: 'x', args: {}, result: 'r', charts: undefined }])
  })

  it('keeps routing and model fallbacks', () => {
    const routing = { type: 'routing', difficulty: 'medium', profile: 'p', tier: 't', model: 'm', reason: 'r' }
    const fb = { type: 'model_fallback', from_model: 'a', to_model: 'b', category: 'rate' }
    const t = run([routing, fb])
    expect(t.activity.routing).toEqual(routing)
    expect(t.activity.fallbacks).toEqual([fb])
  })

  it('error adds an error block with its code', () => {
    const t = run([{ type: 'error', message: 'boom', code: 'no_model' }])
    expect(t.blocks).toEqual([{ kind: 'error', message: 'boom', code: 'no_model' }])
  })

  it('done sets finished', () => {
    expect(run([{ type: 'done' }]).finished).toBe(true)
  })

  it('leaves the turn equal for team-only, usage, conversation and unknown events', () => {
    const base = run([tok('x')])
    const ignored: ChatEvent[] = [
      { type: 'plan', tasks: [] },
      { type: 'plan_updated', tasks: [] },
      { type: 'wave', wave: 1 },
      { type: 'review' },
      { type: 'task_started' },
      { type: 'spoke' },
      { type: 'synthesize' },
      { type: 'needs_human', question: 'q' },
      { type: 'charts', charts: [chart('c')] },
      tok('t', { task: 'T1' }),
      tok('t', { task: 'T1', agent: 'sql' }),
      { type: 'tool_call', name: 'x', args: {}, task: 'T1' },
      { type: 'tool_result', name: 'x', content: 'r', task: 'T1' },
      { type: 'usage', reply: {}, chat: {} },
      { type: 'conversation', id: 'c' },
      { type: 'something_new' },
    ]
    for (const e of ignored) expect(reduceTurn(base, e)).toEqual(base)
  })
})

describe('endTurn', () => {
  it('without done adds the stream_cut block', () => {
    const t = endTurn(run([tok('partial')]))
    expect(t.running).toBe(false)
    expect(t.blocks).toEqual([
      { kind: 'text', text: 'partial' },
      { kind: 'error', message: '', code: 'stream_cut' },
    ])
  })

  it('after done adds nothing', () => {
    const t = endTurn(run([tok('x'), { type: 'done' }]))
    expect(t.blocks).toEqual([{ kind: 'text', text: 'x' }])
  })

  it('with an error keeps the text and adds the error instead of stream_cut', () => {
    const t = endTurn(run([tok('so far')]), 'network down')
    expect(t.blocks).toEqual([
      { kind: 'text', text: 'so far' },
      { kind: 'error', message: 'network down' },
    ])
  })

  it('drops empty text blocks and clears the step', () => {
    const t = endTurn(
      run([{ type: 'tool_call', name: 'x', args: {} }, { type: 'done' }]),
    )
    expect(t.blocks).toEqual([])
    expect(t.step).toEqual({})
  })
})
