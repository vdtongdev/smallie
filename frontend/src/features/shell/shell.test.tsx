import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi as vitest } from 'vitest'
import App from '@/App'
import { AuthProvider } from '@/features/auth/AuthProvider'
import { LanguageProvider } from '@/i18n'
import { en } from '@/i18n/en'
import { installFetch, json, sseResponse } from '@/test/chatMock'

const me = { user_id: 'u1', name: 'An', username: 'an', is_admin: false, max_accounts: 5 }

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

test('folder button shows the file count and opens the dialog', async () => {
  installFetch({
    'GET /api/documents': () =>
      json([
        { id: 'd1', filename: 'a.pdf', status: 'ready', error: null, chunk_count: 1, image_count: 0, images_unread: 0, has_image: false, created_at: '' },
        { id: 'd2', filename: 'b.pdf', status: 'ready', error: null, chunk_count: 1, image_count: 0, images_unread: 0, has_image: false, created_at: '' },
      ]),
  })
  renderApp()
  const button = await screen.findByRole('button', { name: en.documents })
  await waitFor(() => expect(button).toHaveTextContent('2'))
  await userEvent.click(button)
  expect(await screen.findByRole('heading', { name: en.documents })).toBeInTheDocument()
  expect(screen.getByText('a.pdf')).toBeInTheDocument()
})

test('the old upload strip is gone from the main area', async () => {
  installFetch()
  renderApp()
  await screen.findByRole('button', { name: en.documents })
  expect(screen.queryByRole('button', { name: en.uploadFiles })).toBeNull()
  expect(screen.queryByText(en.noDocuments)).toBeNull()
})

test('folder button shows Files and the count', async () => {
  installFetch({
    'GET /api/documents': () =>
      json([{ id: 'd1', filename: 'a.pdf', status: 'ready', error: null, chunk_count: 1, image_count: 0, images_unread: 0, has_image: false, created_at: '' }]),
  })
  renderApp()
  const button = await screen.findByRole('button', { name: en.documents })
  await waitFor(() => expect(button).toHaveTextContent(`${en.files} 1`))
})
