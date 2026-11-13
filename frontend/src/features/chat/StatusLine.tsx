import { Loader2Icon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useLang } from '@/i18n'
import type { Turn } from '@/lib/turn'
import { reviewNote, waveTotal } from './team'
import type { AgentLabels } from './useAgents'

/** What the Activity button counts for a 1-to-1 turn (app.js stepCount without the team parts). */
export function activityCount(turn: Turn): number {
  const a = turn.activity
  return (
    a.waves.reduce((n, w) => n + w.answers.reduce((m, x) => m + 1 + x.tools.length, 0), 0) +
    a.drawn.reduce((n, x) => n + 1 + x.tools.length, 0) +
    a.tools.length +
    a.asked.reduce((n, x) => n + 1 + x.tools.length, 0) +
    (a.question ? 1 : 0) +
    (a.routing ? 1 : 0) +
    a.fallbacks.length
  )
}

type Props = { turn: Turn; labels: AgentLabels; onOpenActivity(turn: Turn): void }

export default function StatusLine({ turn, labels, onOpenActivity }: Props) {
  const { t } = useLang()
  const count = activityCount(turn)
  const act = turn.activity
  const { step } = turn
  let head = ''
  let tail = ''
  let names: string[] = []
  if (turn.running && step.wave) {
    head = t('waveOf').replace('{n}', String(step.wave.wave)).replace('{max}', String(waveTotal(act)))
    names = step.wave.agents.map(labels.agentLabel)
  } else {
    // A step badge with the same label as the badge before it adds nothing.
    for (const b of turn.badges) {
      const name = b.step ? labels.stepLabel(b.agent, b.step) : labels.agentLabel(b.agent)
      if (!b.step || name !== names.at(-1)) names.push(name)
    }
  }
  if (turn.running) {
    const review = act.waves.at(-1)?.review
    if (!step.wave && step.note !== undefined && review) head = reviewNote(act, review, t as (k: string) => string, labels.agentLabel)
    if (step.writing) head = t('writing')
    else if (step.tool) head = t('usingTool').replace('{name}', step.tool)
  } else if (turn.team && act.waves.length) {
    const n = act.waves.length
    head = n === 1 ? t('teamSummaryOne') : t('teamSummary').replace('{n}', String(n))
    const resolved = act.waves.reduce((m, w) => m + (w.review?.outcomes?.length || 0), 0)
    const diffs = act.waves.reduce((m, w) => m + (w.review?.differences?.length || 0), 0)
    if (resolved) tail = t(resolved === 1 ? 'resolvedOne' : 'resolvedMany').replace('{n}', String(resolved))
    else if (diffs) tail = t(diffs === 1 ? 'differenceOne' : 'differenceMany').replace('{n}', String(diffs))
  }
  if (!turn.running && !names.length && !count && !head && !tail) return null
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
      {turn.running && <Loader2Icon className="size-4 animate-spin" aria-hidden />}
      {names.map((name, i) => (
        <span key={i} className="rounded-full border px-2 py-0.5 text-xs">
          {name}
        </span>
      ))}
      {head && <span>{head}</span>}
      {tail && <span>{tail}</span>}
      {count > 0 && (
        <Button type="button" variant="ghost" size="xs" onClick={() => onOpenActivity(turn)}>
          {`${t('activity')} (${count})`}
        </Button>
      )}
    </div>
  )
}
