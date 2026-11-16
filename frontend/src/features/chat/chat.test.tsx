import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from '@/App'
import { cn } from '@/lib/utils'
import { buttonVariants } from '@/components/ui/button'
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

test('a 401 from the chat stream shows the login screen', async () => {
  installFetch({ 'POST /api/chat/auto': () => json({ detail: 'Not authenticated' }, 401) })
  renderApp()
  await userEvent.type(await input(), 'q{Enter}')
  expect(await screen.findByRole('button', { name: en.login })).toBeInTheDocument()
})

test('the conversation list is reloaded when a send finishes', async () => {
  const calls = installFetch({ 'POST /api/chat/auto': () => sseResponse([{ type: 'token', text: 'ok' }, { type: 'done' }]) })
  renderApp()
  const lists = () => calls.filter((c) => c.method === 'GET' && c.url === '/api/conversations').length
  await input()
  await waitFor(() => expect(lists()).toBe(1)) // the mount load
  const before = lists()
  await userEvent.type(await input(), 'q{Enter}')
  await screen.findByText('ok')
  await waitFor(() => expect(lists()).toBeGreaterThan(before))
})

test('the Usage button stays hidden when all usage is zero and there is no reply', async () => {
  installFetch({
    'POST /api/chat/auto': () =>
      sseResponse([
        { type: 'token', text: 'zero' },
        { type: 'usage', reply: null, chat: { input_tokens: 0, output_tokens: 0, cached_tokens: 0 } },
        { type: 'done' },
      ]),
  })
  renderApp()
  await userEvent.type(await input(), 'q{Enter}')
  await screen.findByText('zero')
  await waitFor(() => expect(screen.getByRole('button', { name: en.send })).not.toBeDisabled())
  expect(screen.queryByRole('button', { name: en.usage })).toBeNull()
})

test('the documents folder button is mounted in the logged-in shell', async () => {
  installFetch()
  renderApp()
  expect(await screen.findByRole('button', { name: en.documents })).toBeInTheDocument()
})

test('the Usage button looks like an outline button and sits at the left', async () => {
  installFetch({
    'POST /api/chat/auto': () =>
      sseResponse([
        { type: 'token', text: 'a' },
        { type: 'usage', reply: { input_tokens: 5, output_tokens: 6, cached_tokens: 0 }, chat: { input_tokens: 5, output_tokens: 6, cached_tokens: 0 } },
        { type: 'done' },
      ]),
  })
  renderApp()
  await userEvent.type(await input(), 'q{Enter}')
  const button = await screen.findByRole('button', { name: en.usage })
  for (const cls of cn(buttonVariants({ variant: 'outline' })).split(' ')) expect(button.className).toContain(cls)
  expect(button.parentElement).toHaveClass('justify-start')
  expect(button.parentElement).not.toHaveClass('justify-end')
})

// Images in messages
const loaded = (attachments: { id: string; filename: string; deleted: boolean }[]) => {
  localStorage.setItem('conversation_auto_u1', 'c1')
  return installFetch({
    'GET /api/conversations': () => json([{ id: 'c1', title: 't', kind: 'auto', updated_at: '' }]),
    'GET /api/conversations/c1/messages': () =>
      json([
        { role: 'user', content: 'what is this', activity: null, attachments },
        { role: 'assistant', content: 'a cat' },
      ]),
  })
}

test('user message shows thumbnails from the image route', async () => {
  loaded([{ id: 'i1', filename: 'cat.png', deleted: false }])
  renderApp()
  const img = await screen.findByAltText('cat.png')
  expect(img).toHaveAttribute('src', '/api/documents/i1/image?size=thumb')
  const text = screen.getByText('what is this')
  expect(img.compareDocumentPosition(text) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

test('clicking a thumbnail opens the full image with its name', async () => {
  loaded([{ id: 'i1', filename: 'cat.png', deleted: false }])
  renderApp()
  await userEvent.click(await screen.findByAltText('cat.png'))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByRole('img')).toHaveAttribute('src', '/api/documents/i1/image?size=full')
  expect(within(dialog).getByText('cat.png')).toBeInTheDocument()
})

test('deleted attachment shows image deleted', async () => {
  loaded([{ id: 'i1', filename: 'cat.png', deleted: true }])
  renderApp()
  expect(await screen.findByText(en.imageDeleted)).toBeInTheDocument()
  expect(screen.getByText('cat.png')).toBeInTheDocument()
  expect(screen.queryByRole('img')).toBeNull()
})

test('thumbnail that fails to load shows no preview', async () => {
  loaded([{ id: 'i1', filename: 'old.png', deleted: false }])
  renderApp()
  fireEvent.error(await screen.findByAltText('old.png'))
  expect(await screen.findByText(en.noPreview)).toBeInTheDocument()
  expect(screen.getByText('old.png')).toBeInTheDocument()
  expect(screen.queryByAltText('old.png')).toBeNull()
})

test('just-sent message shows previews before reload', async () => {
  installFetch({
    'GET /api/documents': () => json([{ id: 'd1', filename: 'a.png', status: 'ready', chunk_count: 1, error: null, has_image: true }]),
    'POST /api/documents': () => json([{ id: 'd1', filename: 'a.png', status: 'ready', chunk_count: 1, error: null, has_image: true }]),
    'POST /api/chat/auto': () => sseResponse([{ type: 'token', text: 'ok' }, { type: 'done' }]),
  })
  URL.createObjectURL = vi.fn(() => 'blob:1')
  URL.revokeObjectURL = vi.fn()
  renderApp()
  await input()
  await userEvent.upload(document.querySelector('input[type="file"]') as HTMLInputElement, new File(['x'], 'a.png', { type: 'image/png' }))
  await waitFor(() => expect(document.querySelector('[data-testid="chip-ready"], img')).toBeTruthy())
  await userEvent.type(await input(), 'look{Enter}')
  const imgs = await screen.findAllByAltText('a.png')
  expect(imgs.some((i) => i.getAttribute('src') === '/api/documents/d1/image?size=thumb')).toBe(true)
})

test('attaching an image in the chat updates the folder count', async () => {
  let listed: object[] = []
  const d = { id: 'd1', filename: 'a.png', status: 'ready', chunk_count: 1, error: null, has_image: true }
  installFetch({
    'GET /api/documents': () => json(listed),
    'POST /api/documents': () => {
      listed = [d]
      return json([d])
    },
  })
  URL.createObjectURL = vi.fn(() => 'blob:1')
  URL.revokeObjectURL = vi.fn()
  renderApp()
  const folder = await screen.findByRole('button', { name: en.documents })
  expect(folder).toHaveTextContent('0')
  await userEvent.upload(document.querySelector('input[type="file"]') as HTMLInputElement, new File(['x'], 'a.png', { type: 'image/png' }))
  await waitFor(() => expect(screen.getByRole('button', { name: en.documents })).toHaveTextContent('1'))
})
