import { useRef } from 'react'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { useLang } from '@/i18n'
import { renderMarkdown } from '@/lib/markdown'
import type { Tool, Turn } from '@/lib/turn'
import { fallbackLine, modelLine, type FallbackInfo, type ModelTrace, type Tr } from './modelLine'
import { useAgents } from './useAgents'

type Props = { turn: Turn; open: boolean; onOpenChange(o: boolean): void }

const cut = (v: unknown) => String(v).slice(0, 120)

// Port of app.js toolBlock: summary line, the call, and the result in its own details when there is one.
function ToolBlock({ tool }: { tool: Tool }) {
  const { t } = useLang()
  const { name, args, result } = tool
  const a = args as Record<string, unknown>
  const shown = a.query ?? a.sql ?? a.title ?? a.urls ?? a.name ?? a.fact ?? JSON.stringify(args)
  return (
    <>
      <details className="rounded border p-2 text-sm">
        <summary className="cursor-pointer">{`${t('usedTool')}: ${name} — ${cut(shown)}`}</summary>
        <pre className="overflow-x-auto whitespace-pre-wrap text-xs">
          {typeof a.query === 'string' ? a.query : JSON.stringify(args, null, 2)}
        </pre>
      </details>
      {result !== null && (
        <details className="rounded border p-2 text-sm">
          <summary className="cursor-pointer">{`${t('result')}: ${name}`}</summary>
          <pre className="overflow-x-auto whitespace-pre-wrap text-xs">{result}</pre>
        </details>
      )}
    </>
  )
}

const meta = 'text-xs text-muted-foreground'

// The model line, its reason and any fallback (app.js modelBlock).
function ModelBlock({ m, tr }: { m: ModelTrace & { model?: string; reason?: string; fallback?: FallbackInfo }; tr: Tr }) {
  const line = modelLine(m, tr)
  return (
    <>
      {line && <div className={meta}>{line}</div>}
      {m.tier && (m.model || m.reason) && <div className={meta}>{[m.model, m.reason].filter(Boolean).join(' — ')}</div>}
      {m.fallback && <div className={`${meta} text-destructive`}>{fallbackLine(m.fallback, tr, m.tier)}</div>}
    </>
  )
}

// Port of app.js renderActivity for 1-to-1 turns; the team sections come with team mode.
export default function ActivityDialog({ turn, open, onOpenChange }: Props) {
  const { lang, t } = useLang()
  const { agentLabel } = useAgents()
  const tr: Tr = { t: t as Tr['t'], lang, agentLabel }
  // Radix only returns focus to a DialogTrigger; this dialog is opened from a button elsewhere.
  const opener = useRef<HTMLElement | null>(null)
  const act = turn.activity
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        aria-describedby={undefined}
        className="max-h-[85vh] overflow-y-auto"
        onOpenAutoFocus={() => {
          opener.current = document.activeElement as HTMLElement
        }}
        onCloseAutoFocus={() => opener.current?.focus()}
      >
        <DialogTitle>{t('activity')}</DialogTitle>
        <div className="space-y-4">
          {(act.routing || act.fallbacks.length > 0) && (
            <section className="space-y-1">
              {act.routing && <ModelBlock m={act.routing} tr={tr} />}
              {act.fallbacks.map((f, i) => (
                <div key={i} className={`${meta} text-destructive`}>
                  {fallbackLine(f, tr, act.routing?.tier)}
                </div>
              ))}
            </section>
          )}
          {act.asked.length > 0 && (
            <section className="space-y-3">
              {act.asked.map((a, i) => (
                <div key={i} className="space-y-1">
                  <span className="rounded-full border px-2 py-0.5 text-xs">{agentLabel(a.agent)}</span>
                  {a.text && <div className="markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(a.text) }} />}
                  {a.tools.map((tool, j) => (
                    <ToolBlock key={j} tool={tool} />
                  ))}
                </div>
              ))}
            </section>
          )}
          {act.tools.length > 0 && (
            <section className="space-y-1">
              {act.tools.map((tool, i) => (
                <ToolBlock key={i} tool={tool} />
              ))}
            </section>
          )}
          {act.question && (
            <section>
              <h3 className="font-medium">{t('yourQuestion')}</h3>
              <p>{act.question}</p>
            </section>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
