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

  it('leaves the turn equal for usage, conversation and unknown events', () => {
    const base = run([tok('x')])
    const ignored: ChatEvent[] = [
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

describe('team turns', () => {
  const tasks = [
    { id: 't1', agent: 'sql', goal: 'prices', depends_on: [] },
    { id: 't2', agent: 'research', goal: 'news', depends_on: [] },
  ]
  const team = (events: ChatEvent[]) => run(events, newTurn('q', true))
  const wave = (n: number, ts: string[], agents: string[]): ChatEvent => ({ type: 'wave', wave: n, kind: 'plan', tasks: ts, agents })
  const spoke = (task: string, agent: string, text: string, tools: object[] = []): ChatEvent => ({ type: 'spoke', task, agent, text, tools })

  it('newTurn has the team fields', () => {
    const t = newTurn('q', true)
    expect(t.team).toBe(true)
    expect(t.synthesized).toBe(false)
    expect(t.activity.plan).toEqual([])
    expect(t.activity.waves).toEqual([])
    expect(t.activity.drawn).toEqual([])
    expect(t.activity.synthesis).toBeNull()
    expect(newTurn('q').team).toBe(false)
  })

  it('plan, wave, token with task, spoke, review, synthesize, token, done', () => {
    const review = { type: 'review', agreements: [], differences: [], challenges: [], follow_ups: [] }
    const t = team([{ type: 'plan', tasks }, wave(1, ['t1', 't2'], ['sql', 'research'])])
    expect(t.activity.plan).toEqual(tasks)
    expect(t.badges).toEqual([{ agent: 'sql' }, { agent: 'research' }])
    expect(t.step.wave?.wave).toBe(1)
    expect(t.activity.waves).toHaveLength(1)
    const started = { type: 'task_started', task: 't1', agent: 'sql', model: 'm' }
    const t2 = run([started, tok('dra', { agent: 'sql', task: 't1' }), tok('ft', { agent: 'sql', task: 't1' })], t)
    expect(t2.activity.waves[0].answers).toEqual([{ task: 't1', agent: 'sql', text: 'draft', tools: [], meta: started }])
    expect(t2.blocks).toEqual([])
    expect(t2.started).toBe(false)
    const t3 = run(
      [
        { type: 'tool_call', name: 'run_sql', args: { q: 1 }, agent: 'sql', task: 't1' },
        { type: 'tool_result', name: 'run_sql', content: 'rows', agent: 'sql', task: 't1' },
        spoke('t1', 'sql', 'final', [{ name: 'run_sql', args: { q: 1 }, result: 'rows' }]),
        review,
      ],
      t2,
    )
    const [w] = t3.activity.waves
    expect(w.answers[0].text).toBe('final')
    expect(w.answers[0].tools).toEqual([{ name: 'run_sql', args: { q: 1 }, result: 'rows' }])
    expect(w.review).toEqual(review)
    expect(t3.step.wave).toBeUndefined()
    expect(t3.step.note).toBeDefined()
    expect(t3.activity.tools).toEqual([])
    const t4 = run([{ type: 'synthesize' }, tok('The answer'), { type: 'done' }], t3)
    expect(t4.synthesized).toBe(true)
    expect(t4.step).toEqual({ writing: true })
    expect(texts(t4)).toEqual(['The answer'])
    expect(t4.finished).toBe(true)
  })

  it('a follow-up in plan_updated is appended without duplicate badges (Review Focus 4)', () => {
    const t = team([
      { type: 'plan', tasks },
      { type: 'plan_updated', tasks: [{ id: 't3', agent: 'sql', goal: 'more', depends_on: [], origin: 'follow_up' }, { id: 't4', agent: 'docs', goal: 'd', depends_on: [] }] },
    ])
    expect(t.activity.plan.map((x) => x.id)).toEqual(['t1', 't2', 't3', 't4'])
    expect(t.badges).toEqual([{ agent: 'sql' }, { agent: 'research' }, { agent: 'docs' }])
  })

  it('two tasks of one agent in a wave keep separate answers (Review Focus 5)', () => {
    const t = team([
      wave(1, ['t1', 't2'], ['sql', 'sql']),
      tok('a', { agent: 'sql', task: 't1' }),
      tok('b', { agent: 'sql', task: 't2' }),
      spoke('t1', 'sql', 'first'),
      spoke('t2', 'sql', 'second'),
    ])
    expect(t.activity.waves[0].answers.map((a) => [a.task, a.text])).toEqual([
      ['t1', 'first'],
      ['t2', 'second'],
    ])
  })

  it('an answer goes to the latest wave that ran the task', () => {
    const t = team([wave(1, ['t1'], ['sql']), spoke('t1', 'sql', 'one'), wave(2, ['t1'], ['sql']), spoke('t1', 'sql', 'again')])
    expect(t.activity.waves[0].answers[0].text).toBe('one')
    expect(t.activity.waves[1].answers[0].text).toBe('again')
  })

  it('a task outside every wave (the chart task) lands in drawn', () => {
    const t = team([
      wave(1, ['t1'], ['sql']),
      tok('x', { agent: 'chart', task: 't9' }),
      { type: 'tool_call', name: 'chart_data', args: {}, agent: 'chart', task: 't9' },
      { type: 'tool_result', name: 'chart_data', content: 'ok', agent: 'chart', task: 't9', charts: [chart('c1')] },
    ])
    expect(t.activity.drawn).toHaveLength(1)
    expect(t.activity.drawn[0].tools[0].charts).toEqual([chart('c1')])
    expect(t.blocks).toEqual([])
    const t2 = run([spoke('t9', 'chart', 'drawn', [{ name: 'chart_data', args: {}, result: 'ok' }])], t)
    expect(t2.activity.drawn[0].tools[0].charts).toEqual([chart('c1')])
    expect(t2.activity.drawn[0].text).toBe('drawn')
  })

  it('a participant without a task is an asked specialist', () => {
    const t = team([tok('x', { agent: 'sql' })])
    expect(t.activity.asked.map((a) => a.agent)).toEqual(['sql'])
  })

  it('a single participant with no write-up: its answer and charts become the reply (Review Focus 1)', () => {
    const c = chart('c1')
    const t = team([
      { type: 'plan', tasks: [tasks[0]] },
      wave(1, ['t1'], ['sql']),
      spoke('t1', 'sql', 'Only answer', [{ name: 'chart_sql', args: {}, result: 'ok', charts: [c] }]),
      { type: 'done' },
    ])
    expect(t.blocks).toEqual([
      { kind: 'charts', charts: [c] },
      { kind: 'text', text: 'Only answer' },
    ])
    expect(t.started).toBe(true)
  })

  it('done after a write-up or streamed text does not reuse an answer', () => {
    const base = [wave(1, ['t1'], ['sql']), spoke('t1', 'sql', 'Only answer')]
    expect(texts(team([...base, { type: 'synthesize' }, tok('W'), { type: 'done' }]))).toEqual(['W'])
    expect(texts(team([...base, tok('W'), { type: 'done' }]))).toEqual(['W'])
    expect(texts(team([...base, { type: 'synthesize' }, { type: 'done' }]))).toEqual([])
  })

  it('needs_human shows the question and sets pending', () => {
    const t = team([wave(1, ['t1'], ['sql']), { type: 'needs_human', question: 'How old are you?' }])
    expect(t.pending).toBe(true)
    expect(t.activity.question).toBe('How old are you?')
    expect(t.blocks).toEqual([{ kind: 'text', text: 'How old are you?' }])
  })

  it('team charts go before the current text block, once per id', () => {
    const t = team([{ type: 'synthesize' }, tok('Write-up'), { type: 'charts', charts: [chart('a')] }, { type: 'charts', charts: [chart('a'), chart('b')] }])
    expect(t.blocks).toEqual([
      { kind: 'charts', charts: [chart('a')] },
      { kind: 'charts', charts: [chart('b')] },
      { kind: 'text', text: 'Write-up' },
    ])
  })

  it('the saved token marker creates no text and no answer', () => {
    const t = team([wave(1, ['t1'], ['sql']), tok('', { agent: 'sql' }), tok('')])
    expect(texts(t)).toEqual([])
    expect(t.activity.asked).toEqual([])
    expect(t.activity.waves[0].answers).toEqual([])
    expect(t.started).toBe(true)
  })

  it('ignores model_fallback and agent events in a team turn', () => {
    const t = team([
      { type: 'plan', tasks },
      { type: 'model_fallback', from_model: 'a', category: 'x' },
      { type: 'agent', name: 'orchestrator' },
    ])
    expect(t.activity.fallbacks).toEqual([])
    expect(t.badges).toEqual([{ agent: 'sql' }, { agent: 'research' }])
  })

  it('spoke keeps the charts of a same-named tool at the same index', () => {
    const c = chart('c1')
    const t = team([
      wave(1, ['t1'], ['chart']),
      { type: 'tool_call', name: 'chart_sql', args: {}, agent: 'chart', task: 't1' },
      { type: 'tool_result', name: 'chart_sql', content: 'ok', agent: 'chart', task: 't1', charts: [c] },
      spoke('t1', 'chart', 'x', [{ name: 'chart_sql', args: {}, result: 'ok' }]),
    ])
    expect(t.activity.waves[0].answers[0].tools[0].charts).toEqual([c])
  })
})
