import { describe, expect, it } from 'vitest'
import { teamActivity } from './teamTranscript'

describe('teamActivity', () => {
  it('builds plan, waves and drawn from the task graph history', () => {
    const act = teamActivity({
      synthesis: { open_points: ['p'], not_done: [] },
      tasks: [
        { id: 't1', agent: 'sql', goal: 'g1', depends_on: [], origin: 'plan', status: 'done' },
        { id: 't2', agent: 'chart', goal: 'g2', depends_on: ['answer'], origin: 'plan' },
      ],
      results: { t1: { model: 'm1', tier: 'standard' } },
      history: [
        { type: 'wave', wave: 1, kind: 'plan', tasks: ['t1'], agents: ['sql'] },
        { type: 'result', task: 't1', agent: 'sql', text: 'rows', tools: [{ name: 'run_sql', args: {}, result: 'r' }] },
        { type: 'review', agreements: ['a'], differences: [] },
        { type: 'result', task: 't2', agent: 'chart', text: 'drawn' },
      ],
    })
    expect(act.plan).toEqual([
      { id: 't1', agent: 'sql', goal: 'g1', depends_on: [], origin: 'plan' },
      { id: 't2', agent: 'chart', goal: 'g2', depends_on: ['answer'], origin: 'plan' },
    ])
    expect(act.waves).toHaveLength(1)
    expect(act.waves[0]).toMatchObject({ wave: 1, kind: 'plan', tasks: ['t1'], agents: ['sql'], review: { type: 'review', agreements: ['a'] } })
    expect(act.waves[0].answers).toEqual([
      { task: 't1', agent: 'sql', text: 'rows', tools: [{ name: 'run_sql', args: {}, result: 'r' }], meta: { model: 'm1', tier: 'standard' } },
    ])
    expect(act.drawn).toEqual([{ task: 't2', agent: 'chart', text: 'drawn', tools: [] }])
    expect(act.synthesis).toEqual({ open_points: ['p'], not_done: [] })
  })

  it('reads the older round table shape (rounds and boards) (Review Focus 3)', () => {
    const answers = [
      { agent: 'sql', text: 'a', tools: [] },
      { agent: 'docs', text: 'b', tools: [] },
    ]
    const act = teamActivity({
      rounds: [
        { round: 1, kind: 'answer', answers },
        { round: 2, kind: 'challenge', answers: [answers[0]] },
      ],
      boards: [{ round: 1, agreements: ['x'] }],
      drawn: [{ agent: 'chart', text: 'c', tools: [] }],
    })
    expect(act.plan).toEqual([])
    expect(act.waves.map((w) => [w.wave, w.kind, w.tasks, w.agents])).toEqual([
      [1, 'answer', [], ['sql', 'docs']],
      [2, 'challenge', [], ['sql']],
    ])
    expect(act.waves[0].review).toEqual({ round: 1, agreements: ['x'] })
    expect(act.waves[1].review).toBeNull()
    expect(act.waves[0].answers).toEqual(answers)
    expect(act.drawn).toHaveLength(1)
    expect(act.synthesis).toBeNull()
  })

  it('an empty transcript gives empty parts', () => {
    expect(teamActivity({})).toEqual({ plan: [], waves: [], drawn: [], synthesis: null })
  })
})
