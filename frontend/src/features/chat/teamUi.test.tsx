import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { LanguageProvider } from '@/i18n'
import { en } from '@/i18n/en'
import { endTurn, newTurn, reduceTurn, type Answer, type Turn, type Wave } from '@/lib/turn'
import type { ChatEvent } from '@/lib/types'
import stock from '@/lib/__fixtures__/team_stock.json'
import ActivityDialog from './ActivityDialog'
import StatusLine, { activityCount } from './StatusLine'

beforeEach(() => localStorage.clear())

const labels = { agentLabel: (a: string) => a.toUpperCase(), stepLabel: (_a: string, s: string) => s }
const tool = { name: 'run_sql', args: {}, result: null }
const answer = (agent: string, extra: Partial<Answer> = {}): Answer => ({ agent, text: '', tools: [], ...extra })
const wave = (n: number, extra: Partial<Wave> = {}): Wave => ({ wave: n, kind: 'plan', tasks: [], agents: [], answers: [], review: null, ...extra })
const teamTurn = (act: Partial<Turn['activity']>, rest: Partial<Turn> = {}): Turn => {
  const turn = newTurn('q', true)
  return { ...turn, activity: { ...turn.activity, ...act }, ...rest }
}
const status = (turn: Turn) =>
  render(
    <LanguageProvider>
      <StatusLine turn={turn} labels={labels} onOpenActivity={() => {}} />
    </LanguageProvider>,
  )
const dialog = (turn: Turn) =>
  render(
    <LanguageProvider>
      <ActivityDialog turn={turn} open onOpenChange={() => {}} />
    </LanguageProvider>,
  )

describe('activityCount with team parts', () => {
  it('counts waves answers with their tools, drawn answers with their tools, and the rest', () => {
    const turn = teamTurn({
      waves: [wave(1, { answers: [answer('sql', { tools: [tool, tool] }), answer('docs')] })],
      drawn: [answer('chart', { tools: [tool] })],
      tools: [tool],
      question: 'q?',
    })
    // wave (3 + 1) + drawn 2 + tools 1 + question 1
    expect(activityCount(turn)).toBe(8)
  })
})

describe('StatusLine for a team turn', () => {
  const plan = [
    { id: 't1', agent: 'sql', goal: 'g', depends_on: [] },
    { id: 't2', agent: 'docs', goal: 'g', depends_on: ['t1'] },
  ]
  it('running: wave n of max with that wave agents as badges', () => {
    const w = wave(1, { agents: ['sql'] })
    status(teamTurn({ plan, waves: [w] }, { step: { wave: w } }))
    expect(screen.getByText('Wave 1/2')).toBeInTheDocument()
    expect(screen.getByText('SQL')).toBeInTheDocument()
  })
  it('running after a review: the note from the last review', () => {
    const w = wave(1, { review: { challenges: [{ by_task: 't2', to_task: 't1', question: 'q' }], follow_ups: [{ agent: 'chart', goal: 'draw' }] } })
    status(teamTurn({ plan, waves: [w] }, { step: { note: '' } }))
    expect(screen.getByText('DOCS questions SQL · + CHART: draw')).toBeInTheDocument()
  })
  it('running: writing, and using a tool', () => {
    const { unmount } = status(teamTurn({}, { step: { writing: true } }))
    expect(screen.getByText(en.writing)).toBeInTheDocument()
    unmount()
    status(teamTurn({}, { step: { tool: 'run_sql' } }))
    expect(screen.getByText(en.usingTool.replace('{name}', 'run_sql'))).toBeInTheDocument()
  })
  it('finished: wave count then resolved challenges', () => {
    const waves = [wave(1, { review: { outcomes: [{ task: 't1', revised: true }], differences: [] } }), wave(2)]
    status(teamTurn({ plan, waves }, { running: false }))
    expect(screen.getByText(en.teamSummary.replace('{n}', '2'))).toBeInTheDocument()
    expect(screen.getByText(en.resolvedOne.replace('{n}', '1'))).toBeInTheDocument()
  })
  it('finished: one wave and explained differences when nothing was resolved', () => {
    const waves = [wave(1, { review: { differences: [{}, {}], outcomes: [] } })]
    status(teamTurn({ plan, waves }, { running: false }))
    expect(screen.getByText(en.teamSummaryOne)).toBeInTheDocument()
    expect(screen.getByText(en.differenceMany.replace('{n}', '2'))).toBeInTheDocument()
  })
})

describe('ActivityDialog team sections', () => {
  it('shows the plan with dependencies and marks', () => {
    dialog(
      teamTurn({
        plan: [
          { id: 't1', agent: 'sql', goal: 'get prices', depends_on: [] },
          { id: 't2', agent: 'docs', goal: 'read', depends_on: ['t1'], origin: 'follow_up' },
          { id: 't3', agent: 'chart', goal: 'draw', depends_on: ['t1', 't2'], origin: 'reply' },
        ],
      }),
    )
    expect(screen.getByRole('heading', { name: en.plan })).toBeInTheDocument()
    expect(screen.getByText('t1 sql: get prices')).toBeInTheDocument()
    expect(screen.getByText(`t2 docs: read ← t1 (${en.followUpMark})`)).toBeInTheDocument()
    expect(screen.getByText(`t3 chart: draw ← t1, t2 (${en.replyMark})`)).toBeInTheDocument()
  })

  it('shows a wave with its answer, model line, text, tools and board', () => {
    const review = {
      forced: true,
      agreements: ['same price'],
      differences: [{ routes: ['sql', 'docs'], explanation: 'dates differ', sides: [{ route: 'sql', value: '5', source: 'db', as_of: 'May' }] }],
      conflicts: [{ routes: ['sql'], claim: 'wrong', issue: 'no source', fixable_by: ['sql', 'docs'] }, { routes: ['docs'], claim: 'bad', fixable_by: [] }],
      challenges: [{ by_task: 't2', to_task: 't1', question: 'why?' }],
      outcomes: [{ task: 't1', revised: true }, { task: 't2', revised: false }],
      follow_ups: [{ agent: 'chart', goal: 'draw' }],
    }
    const w = wave(2, {
      kind: 'challenge',
      answers: [answer('sql', { text: 'some **bold**', tools: [tool], meta: { tier: 'small', profile: 'balanced', difficulty: 'easy' } })],
      review,
    })
    dialog(
      teamTurn({
        plan: [
          { id: 't1', agent: 'sql', goal: 'g', depends_on: [] },
          { id: 't2', agent: 'docs', goal: 'g', depends_on: [] },
        ],
        waves: [w],
      }),
    )
    expect(screen.getByRole('heading', { name: `${en.waveN.replace('{n}', '2')} · ${en.kindChallenge}` })).toBeInTheDocument()
    expect(screen.getByText('SQL · easy task · Balanced → small model'.replace('SQL', 'sql'))).toBeInTheDocument()
    expect(screen.getByText('bold').tagName).toBe('STRONG')
    expect(screen.getByText(`${en.usedTool}: run_sql — {}`)).toBeInTheDocument()
    expect(screen.getByText(en.boardTitle)).toBeInTheDocument()
    expect(screen.getByText(en.forcedStop)).toBeInTheDocument()
    for (const k of ['agreements', 'differences', 'challenges', 'outcomes', 'followUps', 'conflicts'] as const) {
      expect(screen.getByText(en[k])).toBeInTheDocument()
    }
    expect(screen.getByText('same price')).toBeInTheDocument()
    expect(screen.getByText('sql, docs: dates differ [sql: 5, db, May]')).toBeInTheDocument()
    expect(screen.getByText('docs -> sql: why?')).toBeInTheDocument()
    expect(screen.getByText(`sql: ${en.revised}`)).toBeInTheDocument()
    expect(screen.getByText(`docs: ${en.stands}`)).toBeInTheDocument()
    expect(screen.getByText('chart: draw')).toBeInTheDocument()
    expect(screen.getByText(`sql: wrong — no source (${en.fixableBy} sql, docs)`)).toBeInTheDocument()
    expect(screen.getByText('docs: bad')).toBeInTheDocument()
  })

  it('shows a board without forcedStop or empty sections', () => {
    dialog(teamTurn({ waves: [wave(1, { review: { agreements: [], differences: [], conflicts: [] } })] }))
    expect(screen.getByText(en.boardTitle)).toBeInTheDocument()
    expect(screen.queryByText(en.forcedStop)).not.toBeInTheDocument()
    expect(screen.queryByText(en.agreements)).not.toBeInTheDocument()
  })

  it('shows the drawn section, other steps only with waves, and open points', () => {
    const base = { tools: [tool], drawn: [answer('chart', { text: 'drawn **it**' })], synthesis: { open_points: ['one open', { a: 1 }], not_done: ['skipped'] } }
    const { unmount } = dialog(teamTurn(base))
    expect(screen.getByRole('heading', { name: en.drawing })).toBeInTheDocument()
    expect(screen.getByText('it').tagName).toBe('STRONG')
    expect(screen.queryByText(en.otherSteps)).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: en.openPoints })).toBeInTheDocument()
    expect(screen.getByText('one open')).toBeInTheDocument()
    expect(screen.getByText('{"a":1}')).toBeInTheDocument()
    expect(screen.getByText('skipped')).toBeInTheDocument()
    unmount()
    dialog(teamTurn({ ...base, waves: [wave(1)] }))
    expect(screen.getByRole('heading', { name: en.otherSteps })).toBeInTheDocument()
  })

  it('keeps the old order: plan, waves, drawn, other steps, open points, question', () => {
    dialog(
      teamTurn({
        plan: [{ id: 't1', agent: 'sql', goal: 'g', depends_on: [] }],
        waves: [wave(1)],
        drawn: [answer('chart')],
        tools: [tool],
        synthesis: { open_points: ['p'], not_done: [] },
        question: 'which?',
      }),
    )
    const heads = screen.getAllByRole('heading').map((h) => h.textContent)
    expect(heads).toEqual([en.activity, en.plan, `${en.waveN.replace('{n}', '1')} · ${en.kindAnswer}`, en.drawing, en.otherSteps, en.openPoints, en.yourQuestion])
  })
})

describe('the recorded team_stock turn', () => {
  const events = (stock as { events: ChatEvent[] }).events
  const turn = endTurn(events.reduce(reduceTurn, newTurn('q', true)))

  it('shows wave count, plan items and a board in the status line and the popup', () => {
    const n = turn.activity.waves.length
    expect(n).toBeGreaterThan(1)
    expect(turn.activity.plan.length).toBeGreaterThan(0)
    expect(turn.activity.waves.some((w) => w.review)).toBe(true)
    status(turn)
    expect(screen.getByText(en.teamSummary.replace('{n}', String(n)))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: `${en.activity} (${activityCount(turn)})` })).toBeInTheDocument()
    dialog(turn)
    const d = screen.getAllByRole('dialog').at(-1)!
    expect(within(d).getAllByRole('heading', { name: new RegExp(`^${en.waveN.replace('{n}', '\\d+')} · `) })).toHaveLength(n)
    const first = turn.activity.plan[0]
    expect(within(d).getByText(new RegExp(`^${first.id} ${first.agent}:`))).toBeInTheDocument()
    expect(within(d).getAllByText(en.boardTitle).length).toBeGreaterThan(0)
  })
})
