import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChartSpec } from '@/lib/types'

const instances: { destroy: ReturnType<typeof vi.fn>; toBase64Image: ReturnType<typeof vi.fn>; config: unknown }[] = []
vi.mock('chart.js', () => {
  class Chart {
    static register = vi.fn()
    static defaults = { plugins: { legend: { labels: { generateLabels: () => [] } } } }
    destroy = vi.fn()
    toBase64Image = vi.fn(() => 'data:image/png;base64,AAA')
    config: unknown
    constructor(_canvas: unknown, config: unknown) {
      this.config = config
      instances.push(this)
    }
  }
  return { Chart, registerables: [] }
})

import { LanguageProvider } from '@/i18n'
import ChartCard from './ChartCard'

const spec = (title: string): ChartSpec => ({
  id: 'c1',
  type: 'line',
  title,
  x_label: 'Date',
  y_label: '',
  labels: ['d1', 'd2'],
  series: [
    { name: 'A', values: [1234.5, null] },
    { name: 'B', values: [2, 3] },
  ],
})
const show = (s: ChartSpec) =>
  render(
    <LanguageProvider>
      <ChartCard spec={s} />
    </LanguageProvider>,
  )

beforeEach(() => {
  instances.length = 0
  document.documentElement.className = ''
  document.documentElement.removeAttribute('data-theme')
})

describe('ChartCard', () => {
  it('title, canvas with role img and aria-label', () => {
    show(spec('Prices'))
    expect(screen.getByText('Prices')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Prices' }).tagName).toBe('CANVAS')
  })
  it('data table has header and rows', () => {
    show(spec('Prices'))
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent)
    expect(headers).toEqual(['Date', 'A', 'B'])
    const rows = screen.getAllByRole('row').map((r) => Array.from(r.children).map((c) => c.textContent))
    expect(rows).toEqual([['Date', 'A', 'B'], ['d1', '1,234.5', '2'], ['d2', '–', '3']])
  })
  it.each([
    ['Apple & Pear 2024!', 'Apple-Pear-2024.png'],
    ['--Giá cổ phiếu--', 'Giá-cổ-phiếu.png'],
    ['!!!', 'chart.png'],
  ])('download file name for %s', async (title, file) => {
    const names: string[] = []
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      names.push(this.download)
    })
    show(spec(title))
    await userEvent.click(screen.getByRole('button', { name: 'Download PNG' }))
    expect(names).toEqual([file])
    click.mockRestore()
  })
  it('destroys the chart on unmount', () => {
    const { unmount } = show(spec('T'))
    expect(instances).toHaveLength(1)
    unmount()
    expect(instances[0].destroy).toHaveBeenCalled()
  })
  it('rebuilds when the html class or data-theme changes', async () => {
    show(spec('T'))
    document.documentElement.classList.add('dark')
    await vi.waitFor(() => expect(instances).toHaveLength(2))
    expect(instances[0].destroy).toHaveBeenCalled()
    document.documentElement.setAttribute('data-theme', 'x')
    await vi.waitFor(() => expect(instances).toHaveLength(3))
  })
})
