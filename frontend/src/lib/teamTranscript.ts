import type { Activity, Answer, Wave } from './turn'

type Team = Record<string, any> // eslint-disable-line @typescript-eslint/no-explicit-any

/**
 * A finished team reply's transcript (`message.team`) as the team parts of an Activity. The task graph's
 * workspace keeps `history` (waves, task results, reviews in order); the older round table kept `rounds` and `boards`.
 */
export function teamActivity(team: Team): Pick<Activity, 'plan' | 'waves' | 'drawn' | 'synthesis'> {
  const synthesis = team.synthesis ?? null
  if (!team.history) {
    const boards: Team[] = team.boards ?? []
    const waves: Wave[] = ((team.rounds ?? []) as Team[]).map((r) => ({
      wave: r.round,
      kind: r.kind,
      tasks: [],
      agents: r.answers.map((a: Answer) => a.agent),
      answers: r.answers,
      review: boards.find((b) => b.round === r.round) ?? null,
    }))
    return { plan: [], waves, drawn: team.drawn ?? [], synthesis }
  }
  const plan = ((team.tasks ?? []) as Team[]).map(({ id, agent, goal, depends_on, origin }) => ({ id, agent, goal, depends_on, origin }))
  const waves: Wave[] = []
  const drawn: Answer[] = []
  for (const e of team.history as Team[]) {
    const last = waves.at(-1)
    if (e.type === 'wave') waves.push({ wave: e.wave, kind: e.kind, tasks: e.tasks, agents: e.agents, answers: [], review: null })
    else if (e.type === 'review' && last) last.review = e
    else if (e.type === 'result') {
      const meta = team.results?.[e.task]
      const answer: Answer = { task: e.task, agent: e.agent, text: e.text, tools: e.tools ?? [], ...(meta ? { meta } : {}) }
      ;(last?.tasks.includes(e.task) ? last.answers : drawn).push(answer)
    }
  }
  return { plan, waves, drawn, synthesis }
}
