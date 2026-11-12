import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi as vitest } from 'vitest'
import { LanguageProvider } from '@/i18n'
import { en } from '@/i18n/en'
import { vi } from '@/i18n/vi'
import ReadmeDialog from './ReadmeDialog'
import ReadmeEn from './ReadmeEn'
import ReadmeVi from './ReadmeVi'

const html = readFileSync(resolve(process.cwd(), '../app/static/index.html'), 'utf-8')
const norm = (s: string | null) => (s ?? '').replace(/\s+/g, ' ').trim()

// Block elements are separated by newlines in the old HTML but by nothing in JSX, so compare each text block on its own.
const blocks = (root: Element) => [...root.querySelectorAll('h1,h2,p,li,th,td,button')].map((el) => norm(el.textContent))

function oldBlocks(lang: 'en' | 'vi') {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  const block = doc.querySelector(`#readmeDialog [data-lang="${lang}"]`)!.cloneNode(true) as Element
  block.querySelectorAll('.market-status').forEach((el) => (el.textContent = ''))
  return blocks(block)
}

let status: () => Response
beforeEach(() => {
  localStorage.clear()
  status = () => new Response(JSON.stringify({ stocks: 200, last_trade_date: '2026-11-10', updated_at: null }), { status: 200 })
  vitest.stubGlobal('fetch', vitest.fn(async () => status()))
})
afterEach(() => vitest.unstubAllGlobals())

test('English readme text matches the old UI', () => {
  const { container } = render(<ReadmeEn marketStatus="" onExample={() => {}} />)
  expect(blocks(container)).toEqual(oldBlocks('en'))
})

test('Vietnamese readme text matches the old UI', () => {
  const { container } = render(<ReadmeVi marketStatus="" onExample={() => {}} />)
  expect(blocks(container)).toEqual(oldBlocks('vi'))
})

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
