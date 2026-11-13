import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi as vitest } from 'vitest'
import App from '@/App'
import { AuthProvider } from '@/features/auth/AuthProvider'
import { LanguageProvider } from '@/i18n'
import { en } from '@/i18n/en'
import { installFetch, sseResponse } from '@/test/chatMock'

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
