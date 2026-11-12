import { useEffect, useRef } from 'react'
import { useLang } from '@/i18n'
import type { Turn } from '@/lib/turn'
import type { ChartSpec } from '@/lib/types'
import AssistantBlocks from './AssistantBlocks'
import { uniqueCharts } from './chartConfig'
import StatusLine from './StatusLine'
import UserMessage from './UserMessage'
import type { AgentLabels } from './useAgents'

type Props = {
  turns: Turn[]
  loadError: string | null
  labels: AgentLabels
  onOpenActivity(turn: Turn): void
  onOpenSettings(): void
}

export default function MessageList({ turns, loadError, labels, onOpenActivity, onOpenSettings }: Props) {
  const { t } = useLang()
  const box = useRef<HTMLElement>(null)
  // Every stream event clones the turn; keeping the first object per chart id stops ChartCard rebuilding its chart.
  const stable = useRef(new Map<string, ChartSpec>())
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight
  }, [turns])

  // A chart id is drawn once per chat: later blocks keep only the charts not shown before.
  const shown: ChartSpec[] = []
  const fresh = (specs: ChartSpec[]) => {
    const all = uniqueCharts([...shown, ...specs])
    const added = all.slice(shown.length).map((c) => stable.current.get(c.id) ?? (stable.current.set(c.id, c), c))
    shown.push(...added)
    return added
  }

  return (
    <section ref={box} aria-live="polite" className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-2">
      {!turns.length && !loadError && <p className="text-muted-foreground">{t('welcome')}</p>}
      {loadError && <div className="text-destructive">⚠ {loadError}</div>}
      {turns.map((turn, i) => {
        // Computed here, in order, so the dedupe sees every earlier turn.
        const charts = new Map<object, ChartSpec[]>()
        for (const b of turn.blocks) if (b.kind === 'charts') charts.set(b, fresh(b.charts as ChartSpec[]))
        const thinking = turn.running && (!turn.started || turn.waiting)
        return (
          <div key={i} className="flex flex-col gap-2">
            {turn.user && <UserMessage text={turn.user} />}
            <StatusLine turn={turn} labels={labels} onOpenActivity={onOpenActivity} />
            {thinking && <p className="italic text-muted-foreground">{t('thinking')}</p>}
            <AssistantBlocks
              blocks={turn.blocks}
              freshCharts={(b) => charts.get(b) ?? []}
              onOpenSettings={onOpenSettings}
            />
            {turn.pending && <div className="text-sm text-muted-foreground">{t('waitingForYou')}</div>}
          </div>
        )
      })}
    </section>
  )
}
