import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi as vitest } from 'vitest'
import App from '@/App'
import { en } from '@/i18n/en'
import { vi } from '@/i18n/vi'
import { LanguageProvider } from '@/i18n'
import { api } from '@/lib/api'
import { AuthProvider } from './AuthProvider'

type Handler = (init?: RequestInit) => Response | Promise<Response>
let routes: Record<string, Handler>
let calls: { url: string; init?: RequestInit }[]

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status })
const meBody = { user_id: 'u1', name: 'An', username: 'an', is_admin: false, max_accounts: 5 }

beforeEach(() => {
  localStorage.clear()
  calls = []
  routes = { 'GET /api/me': () => json(401) }
  vitest.stubGlobal(
    'fetch',
    vitest.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init })
      const h = routes[`${init?.method ?? 'GET'} ${url}`]
      if (!h) throw new Error(`unmocked ${url}`)
      return h(init)
    }),
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

function loggedIn() {
  routes['GET /api/me'] = () => json(200, meBody)
}

test('shows login when /me is 401', async () => {
  renderApp()
  expect(await screen.findByLabelText(en.username)).toBeInTheDocument()
  expect(screen.getByLabelText(en.password)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: en.login })).toBeInTheDocument()
})

test('login success shows the user', async () => {
  routes['POST /api/login'] = () => json(200, {})
  renderApp()
  await userEvent.type(await screen.findByLabelText(en.username), 'an')
  await userEvent.type(screen.getByLabelText(en.password), 'secret123')
  routes['GET /api/me'] = () => json(200, meBody)
  await userEvent.click(screen.getByRole('button', { name: en.login }))
  expect(await screen.findByText('An')).toBeInTheDocument()
  expect(screen.queryByLabelText(en.password)).not.toBeInTheDocument()
  const post = calls.find((c) => c.url === '/api/login')!
  expect(JSON.parse(post.init!.body as string)).toEqual({ username: 'an', password: 'secret123' })
})

test('wrong login shows the error', async () => {
  routes['POST /api/login'] = () => json(401, { detail: 'bad' })
  renderApp()
  await userEvent.type(await screen.findByLabelText(en.username), 'an')
  await userEvent.type(screen.getByLabelText(en.password), 'wrongpass')
  await userEvent.click(screen.getByRole('button', { name: en.login }))
  expect(await screen.findByRole('alert')).toHaveTextContent(en.wrongLogin)
  expect(screen.getByLabelText(en.username)).toBeInTheDocument()
})

test('login network error shows the error', async () => {
  routes['POST /api/login'] = () => {
    throw new TypeError('network down')
  }
  renderApp()
  await userEvent.type(await screen.findByLabelText(en.username), 'an')
  await userEvent.type(screen.getByLabelText(en.password), 'secret123')
  await userEvent.click(screen.getByRole('button', { name: en.login }))
  expect(await screen.findByRole('alert')).toHaveTextContent(en.wrongLogin)
})

test('switch to signup and back', async () => {
  renderApp()
  await userEvent.click(await screen.findByRole('button', { name: en.noAccount }))
  expect(screen.getByLabelText(en.name)).toHaveFocus()
  await userEvent.click(screen.getByRole('button', { name: en.haveAccount }))
  expect(screen.getByLabelText(en.username)).toHaveFocus()
  expect(screen.queryByLabelText(en.name)).not.toBeInTheDocument()
})

async function fillSignup() {
  await userEvent.click(await screen.findByRole('button', { name: en.noAccount }))
  await userEvent.type(screen.getByLabelText(en.name), 'An')
  await userEvent.type(screen.getByLabelText(en.username), 'an_1')
  await userEvent.type(screen.getByLabelText(en.password), 'secret123')
  await userEvent.click(screen.getByRole('button', { name: en.createAccount }))
}

test('signup 409 shows err_taken', async () => {
  routes['POST /api/signup'] = () => json(409, { detail: 'taken' })
  renderApp()
  await fillSignup()
  expect(await screen.findByRole('alert')).toHaveTextContent(en.err_taken)
})

test('signup success shows the user', async () => {
  routes['POST /api/signup'] = () => json(200, {})
  renderApp()
  await userEvent.click(await screen.findByRole('button', { name: en.noAccount }))
  routes['GET /api/me'] = () => json(200, meBody)
  await userEvent.type(screen.getByLabelText(en.name), 'An')
  await userEvent.type(screen.getByLabelText(en.username), 'an_1')
  await userEvent.type(screen.getByLabelText(en.password), 'secret123')
  await userEvent.click(screen.getByRole('button', { name: en.createAccount }))
  expect(await screen.findByText('An')).toBeInTheDocument()
  const post = calls.find((c) => c.url === '/api/signup')!
  expect(JSON.parse(post.init!.body as string)).toEqual({ name: 'An', username: 'an_1', password: 'secret123' })
})

test('logout returns to login', async () => {
  loggedIn()
  routes['POST /api/logout'] = () => json(204)
  renderApp()
  await screen.findByText('An')
  routes['GET /api/me'] = () => json(401)
  await userEvent.click(screen.getByRole('button', { name: en.logout }))
  expect(await screen.findByLabelText(en.username)).toBeInTheDocument()
  expect(calls.some((c) => c.url === '/api/logout' && c.init?.method === 'POST')).toBe(true)
})

test('a later 401 returns to login', async () => {
  loggedIn()
  routes['GET /api/usage'] = () => json(401)
  renderApp()
  await screen.findByText('An')
  await expect(api('/usage')).rejects.toThrow()
  expect(await screen.findByLabelText(en.username)).toBeInTheDocument()
})

test('language toggle relabels the login form', async () => {
  renderApp()
  await screen.findByLabelText(en.username)
  await userEvent.click(screen.getByRole('button', { name: 'Tiếng Việt' }))
  expect(screen.getByRole('button', { name: vi.login })).toBeInTheDocument()
  expect(screen.getByLabelText(vi.username)).toBeInTheDocument()
})

test('login field limits', async () => {
  renderApp()
  expect(await screen.findByLabelText(en.username)).toHaveAttribute('maxlength', '64')
  expect(screen.getByLabelText(en.password)).toHaveAttribute('maxlength', '128')
})

test('signup field limits', async () => {
  renderApp()
  await userEvent.click(await screen.findByRole('button', { name: en.noAccount }))
  const name = screen.getByLabelText(en.name)
  expect(name).toBeRequired()
  expect(name).toHaveAttribute('maxlength', '80')
  expect(name).toHaveAttribute('autocomplete', 'name')
  const user = screen.getByLabelText(en.username)
  expect(user).toBeRequired()
  expect(user).toHaveAttribute('minlength', '3')
  expect(user).toHaveAttribute('maxlength', '32')
  expect(user).toHaveAttribute('pattern', '[A-Za-z0-9_\\-]{3,32}')
  expect(user).toHaveAttribute('autocapitalize', 'none')
  const pw = screen.getByLabelText(en.password)
  expect(pw).toBeRequired()
  expect(pw).toHaveAttribute('minlength', '8')
  expect(pw).toHaveAttribute('maxlength', '128')
  expect(pw).toHaveAttribute('autocomplete', 'new-password')
  expect(screen.getByText(en.usernameHint)).toBeInTheDocument()
  expect(screen.getByText(en.passwordHint)).toBeInTheDocument()
})
