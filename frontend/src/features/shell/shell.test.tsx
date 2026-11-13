import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi as vitest } from 'vitest'
import App from '@/App'
import { AuthProvider } from '@/features/auth/AuthProvider'
import { LanguageProvider } from '@/i18n'
import { en } from '@/i18n/en'
import { installFetch, json, sseResponse } from '@/test/chatMock'

const me = { user_id: 'u1', name: 'An', username: 'an', is_admin: false, max_accounts: 5 }
const usageCalls = (calls: ReturnType<typeof installFetch>) => calls.filter((c) => c.url === '/api/usage').length
const usageBody = () => json({ input_tokens: 1, output_tokens: 1, cost: 0 })

beforeEach(() => {
  localStorage.clear()
  vitest.stubGlobal(
    'fetch',
    vitest.fn(async (url: string) =>
      url === '/api/me'
        ? new Response(JSON.stringify(me), { status: 200 })
        : url === '/api/usage'
          ? new Response(JSON.stringify({ input_tokens: 0, output_tokens: 0, cost: 0 }), { status: 200 })
          : ['/api/documents', '/api/conversations', '/api/agents'].includes(url)
            ? new Response('[]', { status: 200 })
            : new Response(JSON.stringify({ stocks: 1, last_trade_date: null, updated_at: null }), { status: 200 }),
    ),
  )
})
afterEach(() => vitest.unstubAllGlobals())

function renderApp() {
  return render(
    <LanguageProvider>
      <AuthProvider>
        <App />
      </AuthProvider>
    </LanguageProvider>,
  )
}

test('header shows name with @username title, Readme and logout', async () => {
  renderApp()
  expect(await screen.findByText('An')).toHaveAttribute('title', '@an')
  expect(screen.getByRole('button', { name: en.readme })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: en.logout })).toBeInTheDocument()
})

test('the sidebar Settings button opens the Settings dialog', async () => {
  renderApp()
  await userEvent.click(await screen.findByRole('button', { name: en.settings }))
  expect(await screen.findByRole('heading', { name: en.settings })).toBeInTheDocument()
  expect(screen.getByLabelText(en.apiKey)).toBeInTheDocument()
})

test('the chat no_model Open Settings button opens the Settings dialog', async () => {
  installFetch({
    'POST /api/chat/auto': () =>
      sseResponse([{ type: 'error', message: 'No model set up', code: 'no_model' }, { type: 'done' }]),
  })
  renderApp()
  await userEvent.type(await screen.findByPlaceholderText(en.placeholder), 'q{Enter}')
  await userEvent.click(await screen.findByRole('button', { name: en.openSettings }))
  expect(await screen.findByRole('heading', { name: en.settings })).toBeInTheDocument()
})

test('Readme button opens the dialog and close button closes it', async () => {
  renderApp()
  await userEvent.click(await screen.findByRole('button', { name: en.readme }))
  expect(await screen.findByRole('heading', { name: 'How to use smallie' })).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: en.close }))
  expect(screen.queryByRole('heading', { name: 'How to use smallie' })).toBeNull()
})

test('the usage box reloads /api/usage after a send finishes', async () => {
  const calls = installFetch({
    'GET /api/usage': usageBody,
    'POST /api/chat/auto': () => sseResponse([{ type: 'token', text: 'hi' }, { type: 'done' }]),
  })
  renderApp()
  await screen.findByText(en.usage)
  const before = usageCalls(calls)
  await userEvent.type(await screen.findByPlaceholderText(en.placeholder), 'q{Enter}')
  await waitFor(() => expect(usageCalls(calls)).toBe(before + 1))
})

test('the usage box reloads after a send that ends in an error too', async () => {
  const calls = installFetch({
    'GET /api/usage': usageBody,
    'POST /api/chat/auto': () => sseResponse([{ type: 'token', text: 'hi' }], new Error('boom')),
  })
  renderApp()
  await screen.findByText(en.usage)
  const before = usageCalls(calls)
  await userEvent.type(await screen.findByPlaceholderText(en.placeholder), 'q{Enter}')
  await waitFor(() => expect(usageCalls(calls)).toBe(before + 1))
})

test('the Accounts button shows for an admin only', async () => {
  installFetch({ 'GET /api/me': () => json({ ...me, is_admin: true }) })
  const view = renderApp()
  expect(await screen.findByRole('button', { name: en.accounts })).toBeInTheDocument()
  view.unmount()
  installFetch()
  renderApp()
  await screen.findByText('An')
  expect(screen.queryByRole('button', { name: en.accounts })).toBeNull()
})

test('the Accounts button opens the accounts dialog', async () => {
  installFetch({ 'GET /api/me': () => json({ ...me, is_admin: true }), 'GET /api/admin/accounts': () => json([]) })
  renderApp()
  await userEvent.click(await screen.findByRole('button', { name: en.accounts }))
  expect(await screen.findByRole('heading', { name: en.accountsTitle })).toBeInTheDocument()
})
