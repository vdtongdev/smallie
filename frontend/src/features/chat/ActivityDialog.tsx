import { useRef } from 'react'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { useLang } from '@/i18n'
import { renderMarkdown } from '@/lib/markdown'
import type { Activity, Answer, Tool, Turn } from '@/lib/turn'
import { fallbackLine, modelLine, type FallbackInfo, type ModelTrace, type Tr } from './modelLine'
import { planAgent, waveTitle } from './team'
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
function ModelBlock({ m, tr, agent }: { m: ModelTrace & { model?: string; reason?: string; fallback?: FallbackInfo }; tr: Tr; agent?: string }) {
  const line = modelLine(m, tr, agent)
  return (
    <>
      {line && <div className={meta}>{line}</div>}
      {m.tier && (m.model || m.reason) && <div className={meta}>{[m.model, m.reason].filter(Boolean).join(' — ')}</div>}
      {m.fallback && <div className={`${meta} text-destructive`}>{fallbackLine(m.fallback, tr, m.tier)}</div>}
    </>
  )
}

/* eslint-disable @typescript-eslint/no-explicit-any */
// Port of app.js boardBlock: one review's comparison.
function BoardBlock({ b, act, tr }: { b: Record<string, any>; act: Pick<Activity, 'plan'>; tr: Tr }) {
  const { t } = tr
  const label = tr.agentLabel!
  const routes = (list: string[]) => list.map(label).join(', ')
  const pa = (id: string) => planAgent(act, id, label)
  const sections: [string, string[]][] = [
    [t('agreements'), b.agreements],
    [
      t('differences'),
      b.differences.map(
        (d: any) =>
          `${routes(d.routes)}: ${d.explanation}` + (d.sides || []).map((x: any) => ` [${label(x.route)}: ${x.value}, ${x.source}, ${x.as_of}]`).join(''),
      ),
    ],
    [t('challenges'), (b.challenges || []).map((c: any) => `${pa(c.by_task)} -> ${pa(c.to_task)}: ${c.question}`)],
    [t('outcomes'), (b.outcomes || []).map((o: any) => `${pa(o.task)}: ${t(o.revised ? 'revised' : 'stands')}`)],
    [t('followUps'), (b.follow_ups || []).map((f: any) => `${label(f.agent)}: ${f.goal}`)],
    [
      t('conflicts'),
      b.conflicts.map(
        (c: any) =>
          `${routes(c.routes)}: ${c.claim}${c.issue ? ` — ${c.issue}` : ''}` + (c.fixable_by.length ? ` (${t('fixableBy')} ${routes(c.fixable_by)})` : ''),
      ),
    ],
  ]
  return (
    <div className="space-y-1 rounded border p-2 text-sm">
      <strong>{t('boardTitle')}</strong>
      {b.forced && <em className="ml-2">{t('forcedStop')}</em>}
      {sections.map(([title, lines]) =>
        lines.length ? (
          <div key={title}>
            <div className={meta}>{title}</div>
            <ul className="list-disc pl-5">
              {lines.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
          </div>
        ) : null,
      )}
    </div>
  )
}
/* eslint-enable @typescript-eslint/no-explicit-any */

// One task's answer under its badge: model block, markdown text, tools.
function AnswerBlock({ a, tr }: { a: Answer; tr: Tr }) {
  return (
    <div className="space-y-1">
      <span className="rounded-full border px-2 py-0.5 text-xs">{tr.agentLabel!(a.agent)}</span>
      {a.meta && <ModelBlock m={a.meta as ModelTrace} tr={tr} agent={a.agent} />}
      {a.text && <div className="markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(a.text) }} />}
      {a.tools.map((tool, j) => (
        <ToolBlock key={j} tool={tool} />
      ))}
    </div>
  )
}

// Port of app.js renderActivity.
export default function ActivityDialog({ turn, open, onOpenChange }: Props) {
  const { lang, t } = useLang()
  const { agentLabel } = useAgents()
  const tr: Tr = { t: t as Tr['t'], lang, agentLabel }
  // Radix only returns focus to a DialogTrigger; this dialog is opened from a button elsewhere.
  const opener = useRef<HTMLElement | null>(null)
  const act = turn.activity
  const openPoints = act.synthesis ? [...act.synthesis.open_points, ...act.synthesis.not_done] : []
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
          {act.plan.length > 0 && (
            <section className="space-y-1">
              <h3 className="font-medium">{t('plan')}</h3>
              <ul className="list-disc pl-5 text-sm">
                {act.plan.map((x) => {
                  const mark = ({ follow_up: t('followUpMark'), reply: t('replyMark') } as Record<string, string>)[x.origin ?? '']
                  return (
                    <li key={x.id}>{`${x.id} ${agentLabel(x.agent)}: ${x.goal}${x.depends_on.length ? ` ← ${x.depends_on.join(', ')}` : ''}${mark ? ` (${mark})` : ''}`}</li>
                  )
                })}
              </ul>
            </section>
          )}
          {act.waves.map((w) => (
            <section key={w.wave} className="space-y-3">
              <h3 className="font-medium">{waveTitle(w, tr.t)}</h3>
              {w.answers.map((a, i) => (
                <AnswerBlock key={i} a={a} tr={tr} />
              ))}
              {w.review && <BoardBlock b={w.review} act={act} tr={tr} />}
            </section>
          ))}
          {act.drawn.length > 0 && (
            <section className="space-y-3">
              <h3 className="font-medium">{t('drawing')}</h3>
              {act.drawn.map((d, i) => (
                <AnswerBlock key={i} a={d} tr={tr} />
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
              {act.waves.length > 0 && <h3 className="font-medium">{t('otherSteps')}</h3>}
              {act.tools.map((tool, i) => (
                <ToolBlock key={i} tool={tool} />
              ))}
            </section>
          )}
          {openPoints.length > 0 && (
            <section>
              <h3 className="font-medium">{t('openPoints')}</h3>
              <ul className="list-disc pl-5">
                {openPoints.map((line, i) => (
                  <li key={i}>{typeof line === 'string' ? line : JSON.stringify(line)}</li>
                ))}
              </ul>
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
