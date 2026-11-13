import { describe, expect, it } from 'vitest'
import type { ChartSpec } from '@/lib/types'
import { chartConfig, formatValue, uniqueCharts } from './chartConfig'

const colors = {
  series: ['#111111', '#222222', '#333333', '#444444', '#555555', '#666666', '#777777', '#888888'],
  card: '#ffffff',
  text: '#000001',
  muted: '#000002',
  grid: '#000003',
}

const base: ChartSpec = {
  id: 'c1',
  type: 'line',
  title: 'T',
  x_label: 'X',
  y_label: 'Y',
  labels: ['a', 'b', 'c'],
  series: [
    { name: 's1', values: [1, 2, 3] },
    { name: 's2', values: [3, null, 1] },
  ],
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const cfg = (spec: ChartSpec, lang: 'en' | 'vi' = 'en'): any => chartConfig(spec, colors, lang)

describe('formatValue', () => {
  it('null is an en dash', () => {
    expect(formatValue(null, 'en')).toBe('–')
    expect(formatValue(undefined, 'en')).toBe('–')
  })
  it('en-US up to 2 decimals', () => {
    expect(formatValue(1234.5678, 'en')).toBe('1,234.57')
  })
  it('vi-VN uses dot grouping and comma decimals', () => {
    expect(formatValue(1234.5, 'vi')).toBe('1.234,5')
  })
  it('compact notation with 1 decimal', () => {
    expect(formatValue(1500000, 'en', true)).toBe('1.5M')
  })
})

describe('chartConfig line', () => {
  it('colours datasets series[0..] in order', () => {
    const c = cfg(base)
    expect(c.type).toBe('line')
    expect(c.data.labels).toEqual(['a', 'b', 'c'])
    expect(c.data.datasets.map((d: { borderColor: string }) => d.borderColor)).toEqual(['#111111', '#222222'])
    expect(c.data.datasets[1]).toMatchObject({
      label: 's2',
      data: [3, null, 1],
      backgroundColor: '#222222',
      borderWidth: 2,
      fill: false,
      pointRadius: 3,
      pointHoverRadius: 4,
      pointBorderColor: '#ffffff',
      pointBorderWidth: 2,
      pointHitRadius: 12,
      tension: 0,
      spanGaps: true,
    })
  })
  it('more than 24 labels hides the points', () => {
    const labels = Array.from({ length: 25 }, (_, i) => String(i))
    const c = cfg({ ...base, labels, series: [{ name: 's', values: labels.map(() => 1) }] })
    expect(c.data.datasets[0].pointRadius).toBe(0)
    const c24 = cfg({ ...base, labels: labels.slice(0, 24), series: [{ name: 's', values: labels.slice(0, 24).map(() => 1) }] })
    expect(c24.data.datasets[0].pointRadius).toBe(3)
  })
  it('index interaction, aspect ratio 2, legend for 2 series at top with line keys', () => {
    const o = cfg(base).options
    expect(o.interaction).toEqual({ mode: 'index', intersect: false })
    expect(o.aspectRatio).toBe(2)
    expect(o.plugins.legend.display).toBe(true)
    expect(o.plugins.legend.position).toBe('top')
    expect(o.plugins.legend.labels).toMatchObject({ color: '#000001', boxWidth: 16, boxHeight: 2 })
    expect(typeof o.plugins.legend.labels.generateLabels).toBe('function')
  })
  it('one series has no legend', () => {
    expect(cfg({ ...base, series: [base.series[0]] }).options.plugins.legend.display).toBe(false)
  })
  it('axes: x category without grid, y not zero-based for lines, compact ticks', () => {
    const { x, y } = cfg(base).options.scales
    expect(x.type).toBe('category')
    expect(x.grid).toEqual({ display: false })
    expect(x.ticks).toMatchObject({ color: '#000002', autoSkip: true, maxTicksLimit: 8, maxRotation: 0 })
    expect(x.title).toEqual({ display: true, text: 'X', color: '#000002' })
    expect(x.border).toEqual({ display: false })
    expect(y.beginAtZero).toBe(false)
    expect(y.grid).toEqual({ color: '#000003', lineWidth: 1 })
    expect(y.title).toEqual({ display: true, text: 'Y', color: '#000002' })
    expect(y.ticks.callback(1500000)).toBe('1.5M')
  })
  it('a null y tick reads as an en dash, like charts.js', () => {
    expect(cfg(base).options.scales.y.ticks.callback(null)).toBe('–')
  })
  it('empty axis labels hide the title', () => {
    expect(cfg({ ...base, x_label: '' }).options.scales.x.title.display).toBe(false)
  })
  it('plugins are surfaceFill and crosshair for lines', () => {
    expect(cfg(base).plugins.map((p: { id: string }) => p.id)).toEqual(['surfaceFill', 'crosshair'])
  })
  it('tooltip label shows value then series name', () => {
    const label = cfg(base).options.plugins.tooltip.callbacks.label
    expect(label({ raw: 1234.5, dataset: { label: 's1' }, label: 'a', parsed: {} })).toBe(' 1,234.5  s1')
    expect(label({ raw: null, dataset: { label: 's1' }, label: 'a', parsed: {} })).toBe(' –  s1')
  })
  it('vi locale in tooltips', () => {
    const label = cfg(base, 'vi').options.plugins.tooltip.callbacks.label
    expect(label({ raw: 1234.5, dataset: { label: 's1' }, label: 'a', parsed: {} })).toBe(' 1.234,5  s1')
  })
})

describe('chartConfig area', () => {
  it('is a line with a washed fill from origin', () => {
    const c = cfg({ ...base, type: 'area' })
    expect(c.type).toBe('line')
    expect(c.data.datasets[0]).toMatchObject({ fill: 'origin', backgroundColor: '#1111111a', borderColor: '#111111' })
    expect(c.options.scales.y.beginAtZero).toBe(true)
    expect(c.plugins).toHaveLength(2)
  })
})

describe('chartConfig bar', () => {
  it('bar datasets and nearest interaction, surfaceFill only', () => {
    const c = cfg({ ...base, type: 'bar' })
    expect(c.type).toBe('bar')
    expect(c.data.datasets[0]).toMatchObject({
      data: [1, 2, 3],
      backgroundColor: '#111111',
      maxBarThickness: 24,
      borderRadius: 4,
      borderSkipped: 'start',
    })
    expect(c.options.interaction).toEqual({ mode: 'nearest', intersect: true })
    expect(c.options.scales.y.beginAtZero).toBe(true)
    expect(c.options.plugins.legend.labels).toMatchObject({ boxWidth: 10, boxHeight: 10 })
    expect(c.options.plugins.legend.labels.generateLabels).toBeUndefined()
    expect(c.plugins.map((p: { id: string }) => p.id)).toEqual(['surfaceFill'])
  })
})

describe('chartConfig scatter', () => {
  it('points are {x, y}, linear x axis, no labels, y not zero-based', () => {
    const c = cfg({ ...base, type: 'scatter', labels: [1, 2, 3], series: [{ name: 's', values: [5, 6, 7] }] })
    expect(c.type).toBe('scatter')
    expect(c.data.labels).toBeUndefined()
    expect(c.data.datasets[0].data).toEqual([{ x: 1, y: 5 }, { x: 2, y: 6 }, { x: 3, y: 7 }])
    expect(c.data.datasets[0]).toMatchObject({ pointRadius: 4, pointHoverRadius: 5, pointBorderColor: '#ffffff' })
    expect(c.options.scales.x.type).toBe('linear')
    expect(c.options.scales.y.beginAtZero).toBe(false)
  })
  it('tooltip uses parsed y', () => {
    const c = cfg({ ...base, type: 'scatter', labels: [1], series: [{ name: 's', values: [5] }] })
    expect(c.options.plugins.tooltip.callbacks.label({ parsed: { y: 5 }, raw: { x: 1, y: 5 }, dataset: { label: 's' }, label: '' })).toBe(' 5  s')
  })
})

describe('chartConfig pie', () => {
  const pie: ChartSpec = { ...base, type: 'pie', labels: ['a', 'b', 'c'], series: [{ name: 'share', values: [1, 2, 3] }] }
  it('one dataset with per-slice colours and a card-coloured 2px gap', () => {
    const c = cfg(pie)
    expect(c.type).toBe('pie')
    expect(c.data.datasets).toEqual([
      { label: 'share', data: [1, 2, 3], backgroundColor: ['#111111', '#222222', '#333333'], borderColor: '#ffffff', borderWidth: 2 },
    ])
    expect(c.options.scales).toEqual({})
    expect(c.options.aspectRatio).toBe(2.2)
    expect(c.options.plugins.legend).toMatchObject({ display: true, position: 'right' })
  })
  it('tooltip uses the slice label', () => {
    expect(cfg(pie).options.plugins.tooltip.callbacks.label({ raw: 2, label: 'b', dataset: { label: 'share' }, parsed: {} })).toBe(' 2  b')
  })
})

describe('uniqueCharts', () => {
  it('keeps the first of each id', () => {
    const a = { ...base, id: 'x' }
    const b = { ...base, id: 'y' }
    expect(uniqueCharts([a, b, { ...a, title: 'dup' }])).toEqual([a, b])
  })
})
