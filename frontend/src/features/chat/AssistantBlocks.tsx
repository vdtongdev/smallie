import { useLayoutEffect, useMemo, useRef, type MouseEvent } from 'react'
import { Button } from '@/components/ui/button'
import { useLang } from '@/i18n'
import { linkCitations, renderMarkdown } from '@/lib/markdown'
import type { Block } from '@/lib/turn'
import type { ChartSpec } from '@/lib/types'
import ChartCard from './ChartCard'

function TextBlock({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const html = useMemo(() => renderMarkdown(text), [text])
  // The citation links are DOM nodes made after the markdown is in; React keeps them while `html` is unchanged.
  useLayoutEffect(() => {
    if (ref.current) linkCitations(ref.current)
  }, [html])

  const onClick = (e: MouseEvent<HTMLDivElement>) => {
    const cite = (e.target as HTMLElement).closest<HTMLElement>('a.cite')
    if (!cite || !ref.current) return
    e.preventDefault()
    const item = [...ref.current.querySelectorAll(':scope > ol')].pop()?.children[Number(cite.dataset.ref) - 1]
    if (!item) return
    item.scrollIntoView({ behavior: 'smooth', block: 'center' })
    item.classList.remove('ref-flash')
    void (item as HTMLElement).offsetWidth // restart the animation on repeated clicks
    item.classList.add('ref-flash')
  }

  // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions
  return <div ref={ref} className="markdown" onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />
}

type Props = {
  blocks: Block[]
  /** The charts of this block that are new in the chat (a chart id shows once per chat). */
  freshCharts(block: Extract<Block, { kind: 'charts' }>): ChartSpec[]
  onOpenSettings(): void
}

export default function AssistantBlocks({ blocks, freshCharts, onOpenSettings }: Props) {
  const { t } = useLang()
  return (
    <>
      {blocks.map((block, i) => {
        if (block.kind === 'text') return block.text ? <TextBlock key={i} text={block.text} /> : null
        if (block.kind === 'charts')
          return (
            <div key={i}>
              {freshCharts(block).map((spec) => (
                <ChartCard key={spec.id} spec={spec} />
              ))}
            </div>
          )
        return (
          <div key={i} className="text-destructive">
            ⚠ {block.code === 'stream_cut' ? t('streamCut') : block.message}
            {block.code === 'no_model' && (
              <Button type="button" variant="link" size="sm" onClick={onOpenSettings}>
                {t('openSettings')}
              </Button>
            )}
          </div>
        )
      })}
    </>
  )
}
