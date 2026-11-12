import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi as vitest } from 'vitest'
import App from '@/App'
import { AuthProvider } from '@/features/auth/AuthProvider'
import { LanguageProvider } from '@/i18n'
import { en } from '@/i18n/en'

const me = { user_id: 'u1', name: 'An', username: 'an', is_admin: false, max_accounts: 5 }

beforeEach(() => {
  localStorage.clear()
  vitest.stubGlobal(
    'fetch',
    vitest.fn(async (url: string) =>
      url === '/api/me'
        ? new Response(JSON.stringify(me), { status: 200 })
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

test('Readme button opens the dialog and close button closes it', async () => {
  renderApp()
  await userEvent.click(await screen.findByRole('button', { name: en.readme }))
  expect(await screen.findByRole('heading', { name: 'How to use smallie' })).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: en.close }))
  expect(screen.queryByRole('heading', { name: 'How to use smallie' })).toBeNull()
})
