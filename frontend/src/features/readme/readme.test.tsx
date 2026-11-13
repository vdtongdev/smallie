import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi as vitest } from 'vitest'
import { LanguageProvider } from '@/i18n'
import { en } from '@/i18n/en'
import { vi } from '@/i18n/vi'
import ReadmeDialog from './ReadmeDialog'
import ReadmeEn from './ReadmeEn'
import ReadmeVi from './ReadmeVi'

let status: () => Response
beforeEach(() => {
  localStorage.clear()
  status = () => new Response(JSON.stringify({ stocks: 200, last_trade_date: '2026-11-10', updated_at: null }), { status: 200 })
  vitest.stubGlobal('fetch', vitest.fn(async () => status()))
})
afterEach(() => vitest.unstubAllGlobals())

const SECTIONS = {
  en: { title: 'How to use smallie', top: ['What is smallie?', 'The agents', 'Chat modes', 'Usage tracking'], agents: ['Docs', 'Stock database', 'Research', 'Chart'] },
  vi: {
    title: 'Hướng dẫn sử dụng smallie',
    top: ['smallie là gì?', 'Các trợ lý', 'Chế độ trò chuyện', 'Theo dõi mức sử dụng'],
    agents: ['Tài liệu', 'Cơ sở dữ liệu cổ phiếu', 'Nghiên cứu', 'Biểu đồ'],
  },
}
const summaryText = (d: Element) => norm(d.querySelector(':scope > summary')?.textContent ?? null)
const norm = (s: string | null) => (s ?? '').replace(/\s+/g, ' ').trim()

for (const lang of ['en', 'vi'] as const) {
  const Readme = lang === 'en' ? ReadmeEn : ReadmeVi
  const want = SECTIONS[lang]

  test(`${lang} readme is a list of closed details with the main sections`, () => {
    const { container } = render(<Readme marketStatus="" onExample={() => {}} />)
    expect(screen.getByRole('heading', { level: 1, name: want.title })).toBeInTheDocument()
    const top = [...container.querySelectorAll(':scope > div > details')]
    expect(top.map(summaryText)).toEqual(want.top)
    const agents = top[1]
    expect([...agents.querySelectorAll(':scope > details')].map(summaryText)).toEqual(want.agents)
    for (const d of container.querySelectorAll('details')) expect(d).not.toHaveAttribute('open')
  })

  test(`${lang} readme shows the Usage button screenshot with alt text`, () => {
    const { container } = render(<Readme marketStatus="" onExample={() => {}} />)
    const usage = container.querySelectorAll(':scope > div > details')[3]
    const img = usage.querySelector('img')!
    expect(img).not.toBeNull()
    expect(img.getAttribute('alt')?.trim()).toBeTruthy()
  })

  test(`${lang} readme no longer explains memory, models or profiles`, () => {
    const { container } = render(<Readme marketStatus="" onExample={() => {}} />)
    const text = norm(container.textContent)
    const gone = lang === 'en' ? ['remembers durable facts', 'Models and profiles', 'Your model server'] : ['ghi nhớ những thông tin lâu dài', 'Model và gói cấu hình', 'Máy chủ model của bạn']
    for (const g of gone) expect(text).not.toContain(g)
    expect(screen.queryByRole('heading', { name: gone[1] })).toBeNull()
  })
}

function renderDialog(onOpenChange = vitest.fn(), onExample = vitest.fn()) {
  render(
    <LanguageProvider>
      <ReadmeDialog open onOpenChange={onOpenChange} onExample={onExample} />
    </LanguageProvider>,
  )
  return { onOpenChange, onExample }
}

test('example click closes and reports the text', async () => {
  const { onOpenChange, onExample } = renderDialog()
  await userEvent.click(screen.getByRole('button', { name: 'What are the 10 largest companies by market cap?' }))
  expect(onExample).toHaveBeenCalledWith('What are the 10 largest companies by market cap?')
  expect(onOpenChange).toHaveBeenCalledWith(false)
})

test('market status shown', async () => {
  renderDialog()
  expect(await screen.findByText('Currently loaded: 200 stocks, prices up to 2026-11-10.')).toBeInTheDocument()
})

test('market status failure leaves the readme', async () => {
  status = () => new Response('boom', { status: 500 })
  renderDialog()
  expect(screen.getByRole('heading', { name: 'How to use smallie' })).toBeInTheDocument()
  await vitest.waitFor(() => expect(fetch).toHaveBeenCalled())
  await new Promise((r) => setTimeout(r, 20))
  expect(screen.queryByText(/Currently loaded/)).toBeNull()
  expect(screen.queryByText(en.marketEmpty)).toBeNull()
})

test('shows Vietnamese readme when lang is vi', () => {
  localStorage.setItem('lang', 'vi')
  renderDialog()
  expect(screen.getByRole('heading', { name: 'Hướng dẫn sử dụng smallie' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: vi.close })).toBeInTheDocument()
})
