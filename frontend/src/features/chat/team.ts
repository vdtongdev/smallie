// Ports of app.js waveTitle, waveTotal, planAgent and reviewNote (lines 262-280).
import type { Activity, Wave } from '@/lib/turn'

type T = (key: string) => string
type Label = (agent: string) => string
type Plan = Pick<Activity, 'plan' | 'waves'>

export const waveTitle = (w: Wave, t: T): string =>
  `${t('waveN').replace('{n}', String(w.wave))} · ${t(({ conflict: 'kindConflict', challenge: 'kindChallenge' } as Record<string, string>)[w.kind] || 'kindAnswer')}`

/** Waves the plan needs so far: its longest dependency chain, at least the waves already run. */
export function waveTotal(act: Plan): number {
  const depth: Record<string, number> = {}
  const of = (task: Activity['plan'][number], seen: string[] = []): number =>
    (depth[task.id] ??=
      1 +
      Math.max(
        0,
        ...task.depends_on.map((d) => {
          const dep = act.plan.find((x) => x.id === d)
          return dep && !seen.includes(d) ? of(dep, [...seen, task.id]) : 0
        }),
      ))
  return Math.max(act.waves.length, ...act.plan.filter((x) => !x.depends_on.includes('answer')).map((x) => of(x)))
}

export const planAgent = (act: Pick<Activity, 'plan'>, id: string, label: Label): string =>
  label(act.plan.find((x) => x.id === id)?.agent ?? id)

/** One line for a review: who questions whom, and the follow-up tasks it adds. */
export function reviewNote(act: Pick<Activity, 'plan'>, review: Record<string, any>, t: T, label: Label): string { // eslint-disable-line @typescript-eslint/no-explicit-any
  return [
    ...(review.challenges || []).map((c: { by_task: string; to_task: string }) =>
      t('questions').replace('{by}', planAgent(act, c.by_task, label)).replace('{to}', planAgent(act, c.to_task, label)),
    ),
    ...(review.follow_ups || []).map((f: { agent: string; goal: string }) => `+ ${label(f.agent)}: ${f.goal}`),
  ].join(' · ')
}
