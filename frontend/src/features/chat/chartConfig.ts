// Port of app/static/charts.js: Chart.js configuration for the server's chart specs.
import { Chart, type ChartConfiguration, type Plugin } from 'chart.js'
import type { Lang } from '@/i18n'
import type { ChartSpec } from '@/lib/types'

export type ChartColors = { series: string[]; card: string; text: string; muted: string; grid: string }

const cssVar = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()

export function readChartColors(): ChartColors {
  return {
    series: Array.from({ length: 8 }, (_, i) => cssVar(`--series-${i + 1}`)),
    card: cssVar('--card'),
    text: cssVar('--foreground'),
    muted: cssVar('--muted-foreground'),
    grid: cssVar('--border'),
  }
}

export function formatValue(value: number | null | undefined, lang: Lang, compact = false): string {
  if (value == null) return '–'
  return new Intl.NumberFormat(
    lang === 'vi' ? 'vi-VN' : 'en-US',
    compact ? { notation: 'compact', maximumFractionDigits: 1 } : { maximumFractionDigits: 2 },
  ).format(value)
}

// Duplicate chart ids in one chat render once: keep the first of each id.
export function uniqueCharts(specs: ChartSpec[]): ChartSpec[] {
  const seen = new Set<string>()
  return specs.filter((s) => !seen.has(s.id) && !!seen.add(s.id))
}

const washed = (hex: string) => `${hex}1a` // ~10% opacity: area fills are a wash, never a block

// A vertical hairline at the hovered x.
const crosshair = (color: string): Plugin => ({
  id: 'crosshair',
  afterDatasetsDraw(chart) {
    const active = chart.tooltip?.getActiveElements?.() || []
    if (!active.length) return
    const { ctx, chartArea } = chart
    ctx.save()
    ctx.strokeStyle = color
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(active[0].element.x, chartArea.top)
    ctx.lineTo(active[0].element.x, chartArea.bottom)
    ctx.stroke()
    ctx.restore()
  },
})

// Paint the card's surface behind the chart, so a downloaded PNG isn't transparent.
const surfaceFill = (color: string): Plugin => ({
  id: 'surfaceFill',
  beforeDraw(chart) {
    const { ctx, width, height } = chart
    ctx.save()
    ctx.fillStyle = color
    ctx.fillRect(0, 0, width, height)
    ctx.restore()
  },
})

// The config is built as loose objects (as in charts.js); Chart.js validates it at runtime.
export function chartConfig(spec: ChartSpec, colors: ChartColors, lang: Lang): ChartConfiguration {
  const { card, text, muted, grid } = colors
  const seriesColor = (i: number) => colors.series[i]
  const many = spec.labels.length > 24
  const isLine = spec.type === 'line' || spec.type === 'area'

  let datasets: unknown[]
  if (spec.type === 'pie') {
    datasets = [
      {
        label: spec.series[0].name,
        data: spec.series[0].values,
        backgroundColor: spec.labels.map((_, i) => seriesColor(i)),
        borderColor: card,
        borderWidth: 2,
      },
    ]
  } else {
    datasets = spec.series.map((series, i) => {
      const color = seriesColor(i)
      const common = { label: series.name, borderColor: color, backgroundColor: color }
      if (spec.type === 'bar') {
        return { ...common, data: series.values, maxBarThickness: 24, borderRadius: 4, borderSkipped: 'start' }
      }
      if (spec.type === 'scatter') {
        return {
          ...common,
          data: spec.labels.map((x, j) => ({ x, y: series.values[j] })),
          pointRadius: 4,
          pointHoverRadius: 5,
          pointBorderColor: card,
          pointBorderWidth: 2,
          pointHitRadius: 12,
        }
      }
      return {
        ...common,
        data: series.values,
        borderWidth: 2,
        borderJoinStyle: 'round',
        borderCapStyle: 'round',
        fill: spec.type === 'area' ? 'origin' : false,
        backgroundColor: spec.type === 'area' ? washed(color) : color,
        pointRadius: many ? 0 : 3,
        pointHoverRadius: 4,
        pointBorderColor: card,
        pointBorderWidth: 2,
        pointHitRadius: 12,
        tension: 0,
        spanGaps: true,
      }
    })
  }

  const axis = (title: string) => ({
    grid: { color: grid, lineWidth: 1 },
    border: { display: false },
    ticks: { color: muted },
    title: { display: !!title, text: title, color: muted },
  })
  const scales =
    spec.type === 'pie'
      ? {}
      : {
          x: {
            ...axis(spec.x_label),
            type: spec.type === 'scatter' ? 'linear' : 'category',
            grid: { display: false },
            ticks: { color: muted, autoSkip: true, maxTicksLimit: 8, maxRotation: 0 },
          },
          y: {
            ...axis(spec.y_label),
            beginAtZero: spec.type !== 'line' && spec.type !== 'scatter',
            ticks: { color: muted, callback: (v: unknown) => formatValue(v as number | null, lang, true) },
          },
        }

  const type = ({ area: 'line', pie: 'pie', bar: 'bar', scatter: 'scatter' } as Record<string, string>)[spec.type] || 'line'
  return {
    type,
    data: { labels: spec.type === 'scatter' ? undefined : spec.labels, datasets },
    plugins: isLine ? [surfaceFill(card), crosshair(muted)] : [surfaceFill(card)],
    options: {
      responsive: true,
      maintainAspectRatio: true,
      aspectRatio: spec.type === 'pie' ? 2.2 : 2,
      animation: { duration: 250 },
      // Lines: the whole x position is the target; bars, dots and slices: the mark itself.
      interaction: isLine ? { mode: 'index', intersect: false } : { mode: 'nearest', intersect: true },
      scales,
      plugins: {
        legend: {
          display: spec.type === 'pie' || spec.series.length > 1,
          position: spec.type === 'pie' ? 'right' : 'top',
          labels: {
            color: text,
            boxWidth: isLine ? 16 : 10,
            boxHeight: isLine ? 2 : 10,
            // Line keys take the line colour.
            ...(isLine && {
              generateLabels: (chart: Chart) =>
                Chart.defaults.plugins.legend.labels.generateLabels(chart).map((item) => ({
                  ...item,
                  fillStyle: item.strokeStyle,
                  lineWidth: 0,
                })),
            }),
          },
        },
        tooltip: {
          callbacks: {
            label: (item: { parsed: { y: number }; raw: unknown; label: string; dataset: { label?: string } }) => {
              const value = spec.type === 'scatter' ? item.parsed.y : (item.raw as number)
              const name = spec.type === 'pie' ? item.label : item.dataset.label
              return ` ${formatValue(value, lang)}  ${name}`
            },
          },
        },
      },
    },
  } as unknown as ChartConfiguration
}
