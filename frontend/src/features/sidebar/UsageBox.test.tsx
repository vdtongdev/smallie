import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { LanguageProvider, useLang } from '@/i18n'
import { en } from '@/i18n/en'
import { localSet } from '@/lib/storage'
import UsageBox from './UsageBox'

function respond(body: unknown, ok = true) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status: ok ? 200 : 500 }))
}

function LangButton() {
  const { setLang } = useLang()
  return <button onClick={() => setLang('vi')}>to-vi</button>
}

function show(refreshKey = 0) {
  const tree = (key: number) => (
    <LanguageProvider>
      <UsageBox refreshKey={key} />
      <LangButton />
    </LanguageProvider>
  )
  const view = render(tree(refreshKey))
  return { rerender: (key: number) => view.rerender(tree(key)) }
}

beforeEach(() => localSet('lang', 'en'))
afterEach(() => vi.unstubAllGlobals())

test('shows total tokens, the in/out detail as title, and the cost', async () => {
  vi.stubGlobal('fetch', respond({ input_tokens: 1200, output_tokens: 34, cost: 0.1234567 }))
  show()
  const tokens = await screen.findByText(en.usageTokens.replace('{n}', '1,234'))
  expect(tokens).toHaveAttribute('title', en.usageDetail.replace('{input}', '1,200').replace('{output}', '34'))
  expect(screen.getByText(en.usageCost.replace('{cost}', '0.1235'))).toBeInTheDocument()
  expect(screen.getByText(en.usage)).toBeInTheDocument()
})

test('a cost under one cent shows six decimals', async () => {
  vi.stubGlobal('fetch', respond({ input_tokens: 10, output_tokens: 5, cost: 0.000015 }))
  show()
  expect(await screen.findByText(en.usageCost.replace('{cost}', '0.000015'))).toBeInTheDocument()
})

test('Vietnamese uses vi-VN number formatting', async () => {
  localSet('lang', 'vi')
  vi.stubGlobal('fetch', respond({ input_tokens: 1200000, output_tokens: 34, cost: 0.5 }))
  show()
  const text = await screen.findByText(/^Token:/)
  expect(text.textContent).toBe('Token: ' + (1200034).toLocaleString('vi-VN'))
})

test('reloads when refreshKey changes', async () => {
  const fetchMock = respond({ input_tokens: 1, output_tokens: 1, cost: 0 })
  vi.stubGlobal('fetch', fetchMock)
  const { rerender } = show(0)
  await screen.findByText(en.usage)
  expect(fetchMock).toHaveBeenCalledTimes(1)
  rerender(1)
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
})

test('reloads when the language changes', async () => {
  const fetchMock = respond({ input_tokens: 1000, output_tokens: 0, cost: 0 })
  vi.stubGlobal('fetch', fetchMock)
  show()
  await screen.findByText(en.usage)
  await userEvent.click(screen.getByText('to-vi'))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
})

test('stays hidden when the load fails', async () => {
  const fetchMock = respond({ detail: 'x' }, false)
  vi.stubGlobal('fetch', fetchMock)
  show()
  await waitFor(() => expect(fetchMock).toHaveBeenCalled())
  expect(screen.queryByText(en.usage)).not.toBeInTheDocument()
})
