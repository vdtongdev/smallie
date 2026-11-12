import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from '@/App'
import { AuthProvider } from '@/features/auth/AuthProvider'
import { LanguageProvider } from '@/i18n'
import { en } from '@/i18n/en'
import { installFetch, json, sseResponse } from '@/test/chatMock'

const renderApp = () =>
  render(
    <LanguageProvider>
      <AuthProvider>
        <App />
      </AuthProvider>
    </LanguageProvider>,
  )
const input = async () => (await screen.findByPlaceholderText(en.placeholder)) as HTMLTextAreaElement
const posts = (calls: { method: string }[]) => calls.filter((c) => c.method === 'POST')
const gate = () => {
  let release!: () => void
  const wait = new Promise<void>((r) => (release = r))
  return { wait, release }
}

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

test('Enter sends and clears the input', async () => {
  const calls = installFetch({ 'POST /api/chat/auto': () => sseResponse([{ type: 'token', text: 'ok' }, { type: 'done' }]) })
  renderApp()
  const box = await input()
  await userEvent.type(box, 'hello{Enter}')
  expect(box.value).toBe('')
  expect(await screen.findByText('ok')).toBeInTheDocument()
  expect(screen.getByText('hello')).toBeInTheDocument()
  expect(posts(calls)).toHaveLength(1)
})

test('Shift+Enter makes a newline and does not send', async () => {
  const calls = installFetch()
  renderApp()
  const box = await input()
  await userEvent.type(box, 'a{Shift>}{Enter}{/Shift}b')
  expect(box.value).toBe('a\nb')
  expect(posts(calls)).toHaveLength(0)
})

test('Enter while composing (IME) does not send', async () => {
  const calls = installFetch()
  renderApp()
  const box = await input()
  fireEvent.change(box, { target: { value: 'xin chào' } })
  fireEvent.keyDown(box, { key: 'Enter', isComposing: true })
  expect(posts(calls)).toHaveLength(0)
  expect(box.value).toBe('xin chào')
})

test('a blank input is not sent', async () => {
  const calls = installFetch()
  renderApp()
  const box = await input()
  await userEvent.type(box, '   {Enter}')
  expect(posts(calls)).toHaveLength(0)
})

test('a Readme example fills the input', async () => {
  installFetch()
  renderApp()
  await userEvent.click(await screen.findByRole('button', { name: en.readme }))
  await userEvent.click(await screen.findByRole('button', { name: 'What are the 10 largest companies by market cap?' }))
  expect((await input()).value).toBe('What are the 10 largest companies by market cap?')
})

test('thinking shows first and the answer replaces it', async () => {
  const g = gate()
  installFetch({
    'POST /api/chat/auto': async () => {
      await g.wait
      return sseResponse([{ type: 'token', text: 'The answer' }, { type: 'done' }])
    },
  })
  renderApp()
  await userEvent.type(await input(), 'q{Enter}')
  expect(await screen.findByText(en.thinking)).toBeInTheDocument()
  act(() => g.release())
  expect(await screen.findByText('The answer')).toBeInTheDocument()
  expect(screen.queryByText(en.thinking)).toBeNull()
})

test('a citation click scrolls its list item into view', async () => {
  const scrollIntoView = vi.fn()
  Element.prototype.scrollIntoView = scrollIntoView
  installFetch({
    'POST /api/chat/auto': () =>
      sseResponse([{ type: 'token', text: 'Fact.[1]\n\n1. https://example.com/a\n2. https://example.com/b\n' }, { type: 'done' }]),
  })
  const { container } = renderApp()
  await userEvent.type(await input(), 'q{Enter}')
  const cite = await waitFor(() => {
    const a = container.querySelector('a.cite')
    expect(a).not.toBeNull()
    return a as HTMLElement
  })
  await userEvent.click(cite)
  expect(scrollIntoView).toHaveBeenCalledTimes(1)
  expect(scrollIntoView.mock.contexts[0]).toBe(container.querySelector('ol')!.children[0])
})

test('a no_model error shows the Open Settings button', async () => {
  installFetch({
    'POST /api/chat/auto': () =>
      sseResponse([{ type: 'error', message: 'No model set up', code: 'no_model' }, { type: 'done' }]),
  })
  renderApp()
  await userEvent.type(await input(), 'q{Enter}')
  expect(await screen.findByText(/No model set up/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: en.openSettings })).toBeInTheDocument()
})

test('a stream cut shows the connection-lost text', async () => {
  installFetch({ 'POST /api/chat/auto': () => sseResponse([{ type: 'token', text: 'half' }]) })
  renderApp()
  await userEvent.type(await input(), 'q{Enter}')
  expect(await screen.findByText(new RegExp(en.streamCut.slice(0, 30)))).toBeInTheDocument()
  expect(await input()).not.toBeDisabled()
})

test('the Activity button shows the count and the status line labels badges from /api/agents', async () => {
  installFetch({
    'GET /api/agents': () => json([{ route: 'sql', labels: { en: 'SQL agent', vi: 'Agent SQL' }, steps: {} }]),
    'POST /api/chat/auto': () =>
      sseResponse([
        { type: 'agent', name: 'sql' },
        { type: 'tool_call', name: 'run_sql', args: { sql: 'select 1' } },
        { type: 'tool_result', name: 'run_sql', content: '1' },
        { type: 'token', text: 'done it' },
        { type: 'done' },
      ]),
  })
  renderApp()
  await userEvent.type(await input(), 'q{Enter}')
  expect(await screen.findByText('SQL agent')).toBeInTheDocument()
  expect(await screen.findByRole('button', { name: `${en.activity} (1)` })).toBeInTheDocument()
})

test('the Usage button is hidden without usage and opens the dialog once there is some', async () => {
  installFetch({
    'POST /api/chat/auto': () =>
      sseResponse([
        { type: 'token', text: 'a' },
        { type: 'usage', reply: { input_tokens: 5, output_tokens: 6, cached_tokens: 0 }, chat: { input_tokens: 5, output_tokens: 6, cached_tokens: 0 } },
        { type: 'done' },
      ]),
  })
  renderApp()
  await input()
  expect(screen.queryByRole('button', { name: en.usage })).toBeNull()
  await userEvent.type(await input(), 'q{Enter}')
  await userEvent.click(await screen.findByRole('button', { name: en.usage }))
  expect(within(await screen.findByRole('dialog')).getByText(en.usageChat)).toBeInTheDocument()
})

test('a chart id that appears twice in one chat renders once', async () => {
  const spec = { id: 'c1', type: 'bar', title: 'Sales', x_label: '', y_label: '', labels: ['a'], series: [{ name: 's', values: [1] }] }
  installFetch({
    'POST /api/chat/auto': () =>
      sseResponse([
        { type: 'tool_call', name: 'chart', args: {} },
        { type: 'tool_result', name: 'chart', content: 'ok', charts: [spec] },
        { type: 'tool_call', name: 'chart', args: {} },
        { type: 'tool_result', name: 'chart', content: 'ok', charts: [spec] },
        { type: 'token', text: 'fin' },
        { type: 'done' },
      ]),
  })
  renderApp()
  await userEvent.type(await input(), 'q{Enter}')
  await screen.findByText('fin')
  expect(screen.getAllByText('Sales')).toHaveLength(1)
})
