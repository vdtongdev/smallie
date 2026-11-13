import { describe, expect, it } from 'vitest'
import { en } from '@/i18n/en'
import type { Activity, PlanTask, Wave } from '@/lib/turn'
import { planAgent, reviewNote, waveTitle, waveTotal } from './team'

const t = (k: string) => (en as Record<string, string>)[k] ?? k
const label = (a: string) => a.toUpperCase()
const task = (id: string, depends_on: string[] = [], agent = 'sql'): PlanTask => ({ id, agent, goal: 'g', depends_on })
const wave = (n: number, kind = 'plan'): Wave => ({ wave: n, kind, tasks: [], agents: [], answers: [], review: null })
const act = (plan: PlanTask[], waves: Wave[] = []) => ({ plan, waves }) as Pick<Activity, 'plan' | 'waves'>

describe('waveTitle', () => {
  it('names the wave and its kind', () => {
    expect(waveTitle(wave(2), t)).toBe(`${en.waveN.replace('{n}', '2')} · ${en.kindAnswer}`)
    expect(waveTitle(wave(1, 'challenge'), t)).toBe(`${en.waveN.replace('{n}', '1')} · ${en.kindChallenge}`)
    expect(waveTitle(wave(3, 'conflict'), t)).toBe(`${en.waveN.replace('{n}', '3')} · ${en.kindConflict}`)
    expect(waveTitle(wave(3, 'odd'), t)).toContain(en.kindAnswer)
  })
})

describe('waveTotal', () => {
  it('is the longest dependency chain', () => {
    expect(waveTotal(act([task('t1'), task('t2', ['t1']), task('t3', ['t2'])]))).toBe(3)
    expect(waveTotal(act([task('t1'), task('t2')]))).toBe(1)
  })
  it('is at least the waves already run', () => {
    expect(waveTotal(act([task('t1')], [wave(1), wave(2)]))).toBe(2)
  })
  it('ignores tasks that wait for the answer', () => {
    expect(waveTotal(act([task('t1'), task('t2', ['answer'])]))).toBe(1)
    expect(waveTotal(act([task('t1'), task('t2', ['t1', 'answer'])]))).toBe(1)
  })
  it('survives a dependency cycle and an unknown dependency', () => {
    expect(waveTotal(act([task('t1', ['t2']), task('t2', ['t1'])]))).toBe(2)
    expect(waveTotal(act([task('t1', ['zz'])]))).toBe(1)
  })
  it('is the wave count with no plan', () => {
    expect(waveTotal(act([], [wave(1)]))).toBe(1)
  })
})

describe('planAgent', () => {
  it('labels the task agent, or the id itself when unknown', () => {
    const a = act([task('t1', [], 'docs')])
    expect(planAgent(a, 't1', label)).toBe('DOCS')
    expect(planAgent(a, 'x9', label)).toBe('X9')
  })
})

describe('reviewNote', () => {
  const a = act([task('t1', [], 'sql'), task('t2', [], 'docs')])
  it('joins challenges and follow-ups', () => {
    const review = {
      challenges: [{ by_task: 't1', to_task: 't2', question: 'q' }],
      follow_ups: [{ agent: 'chart', goal: 'draw it' }],
    }
    expect(reviewNote(a, review, t, label)).toBe(`${en.questions.replace('{by}', 'SQL').replace('{to}', 'DOCS')} · + CHART: draw it`)
  })
  it('is empty for a review with neither', () => {
    expect(reviewNote(a, {}, t, label)).toBe('')
  })
})
