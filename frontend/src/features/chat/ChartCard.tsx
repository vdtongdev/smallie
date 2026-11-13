import { Chart, registerables } from 'chart.js'
import { useEffect, useRef } from 'react'
import { useLang } from '@/i18n'
import type { ChartSpec } from '@/lib/types'
import { chartConfig, formatValue, readChartColors } from './chartConfig'

Chart.register(...registerables)

export const chartFileName = (title: string) =>
  `${title.replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '') || 'chart'}.png`

export default function ChartCard({ spec }: { spec: ChartSpec }) {
  const { lang, t } = useLang()
  const canvas = useRef<HTMLCanvasElement>(null)
  const chart = useRef<Chart | null>(null)

  useEffect(() => {
    const build = () => {
      chart.current?.destroy()
      chart.current = new Chart(canvas.current!, chartConfig(spec, readChartColors(), lang))
    }
    build()
    // Dark and light steps are different colours, not a flip: rebuild on a theme change.
    const observer = new MutationObserver(build)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] })
    return () => {
      observer.disconnect()
      chart.current?.destroy()
      chart.current = null
    }
  }, [spec, lang])

  const download = () => {
    if (!chart.current) return
    const link = document.createElement('a')
    link.href = chart.current.toBase64Image('image/png')
    link.download = chartFileName(spec.title)
    link.click()
  }

  return (
    <figure className="my-3 rounded-lg border bg-card p-3">
      <figcaption className="mb-2 font-medium">{spec.title}</figcaption>
      <div>
        <canvas ref={canvas} role="img" aria-label={spec.title} />
      </div>
      <div className="mt-2 flex flex-wrap items-start gap-4 text-sm">
        <button type="button" className="underline" onClick={download}>
          {t('downloadPng')}
        </button>
        <details>
          <summary className="cursor-pointer">{t('showData')}</summary>
          <table className="mt-2 text-xs">
            <thead>
              <tr>
                {[spec.x_label || '', ...spec.series.map((s) => s.name)].map((name, i) => (
                  <th key={i} className="px-2 text-left">
                    {name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {spec.labels.map((label, i) => (
                <tr key={i}>
                  <td className="px-2">{label}</td>
                  {spec.series.map((s, j) => (
                    <td key={j} className="px-2">
                      {formatValue(s.values[i], lang)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      </div>
    </figure>
  )
}
