import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi as vitest } from 'vitest'
import App from './App'
import { vi } from './i18n/vi'
import { AuthProvider } from './features/auth/AuthProvider'
import { LanguageProvider } from './i18n'

function renderApp() {
  return render(
    <LanguageProvider>
      <AuthProvider>
        <App />
      </AuthProvider>
    </LanguageProvider>,
  )
}

beforeEach(() => {
  vitest.stubGlobal('fetch', vitest.fn(async () => new Response(null, { status: 401 })))
  localStorage.clear()
  document.documentElement.lang = ''
})
afterEach(() => {
  vitest.restoreAllMocks()
  vitest.unstubAllGlobals()
})

test('renders the brand', () => {
  renderApp()
  expect(screen.getByRole('heading', { name: 'smallie' })).toBeInTheDocument()
})

test('defaults to English and shows the Vietnamese toggle', () => {
  renderApp()
  expect(screen.getByRole('button', { name: 'Tiếng Việt' })).toBeInTheDocument()
  expect(document.documentElement.lang).toBe('en')
})

test('toggle switches to Vietnamese and remembers it', async () => {
  vitest.stubGlobal(
    'fetch',
    vitest.fn(async (url: string) =>
      url === '/api/me'
        ? new Response(JSON.stringify({ user_id: 'u1', name: 'An', username: 'an', is_admin: false, max_accounts: 10 }), { status: 200 })
        : url === '/api/usage'
          ? new Response(JSON.stringify({ input_tokens: 0, output_tokens: 0, cost: 0 }), { status: 200 })
          : new Response('[]', { status: 200 }),
    ),
  )
  renderApp()
  await screen.findByText('An')
  await userEvent.click(screen.getByRole('button', { name: 'Tiếng Việt' }))
  expect(screen.getByRole('button', { name: 'English' })).toBeInTheDocument()
  expect(localStorage.getItem('lang')).toBe('vi')
  expect(screen.getByText(vi.welcome)).toBeInTheDocument()
  expect(document.documentElement.lang).toBe('vi')
})

test('stored vi is used on load', () => {
  localStorage.setItem('lang', 'vi')
  renderApp()
  expect(screen.getByRole('button', { name: 'English' })).toBeInTheDocument()
})

test('unknown stored value falls back to English', () => {
  localStorage.setItem('lang', 'fr')
  renderApp()
  expect(screen.getByRole('button', { name: 'Tiếng Việt' })).toBeInTheDocument()
})

test('blocked storage still renders in English', () => {
  vitest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('blocked')
  })
  renderApp()
  expect(screen.getByRole('button', { name: 'Tiếng Việt' })).toBeInTheDocument()
})
