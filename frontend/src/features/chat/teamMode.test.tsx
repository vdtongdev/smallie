import { render, screen, waitFor } from '@testing-library/react'
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
const posts = (calls: { method: string; url: string }[]) => calls.filter((c) => c.method === 'POST').map((c) => c.url)
const sw = () => screen.findByRole('checkbox', { name: en.teamMode })
const done = [{ type: 'token', text: 'ok' }, { type: 'done' }]

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

test('the switch is stored as team_mode and restored on load', async () => {
  installFetch()
  const first = renderApp()
  const box = await sw()
  expect(box).not.toBeChecked()
  await userEvent.click(box)
  expect(localStorage.getItem('team_mode')).toBe('1')
  expect(box).toBeChecked()
  await userEvent.click(box)
  expect(localStorage.getItem('team_mode')).toBe('0')
  await userEvent.click(box)
  first.unmount()
  renderApp()
  expect(await sw()).toBeChecked()
})

test('a new chat with the switch on posts to the team endpoint', async () => {
  localStorage.setItem('team_mode', '1')
  const calls = installFetch({ 'POST /api/chat/team': () => sseResponse([{ type: 'conversation', id: 'c1' }, ...done]) })
  renderApp()
  await userEvent.type(await screen.findByPlaceholderText(en.placeholder), 'q{Enter}')
  await screen.findByText('ok')
  expect(posts(calls)).toEqual(['/api/chat/team'])
})

test("the switch is disabled while a chat is open and shows that chat's kind", async () => {
  installFetch({
    'GET /api/conversations': () =>
      json([
        { id: 'a', title: 'Plain', kind: 'auto' },
        { id: 't', title: 'Group', kind: 'team' },
      ]),
    'GET /api/conversations/t/messages': () => json([]),
    'GET /api/conversations/a/messages': () => json([]),
  })
  renderApp()
  const box = await sw()
  expect(box).toBeEnabled()
  await userEvent.click(await screen.findByRole('button', { name: 'Group' }))
  await waitFor(() => expect(box).toBeDisabled())
  expect(box).toBeChecked()
  await userEvent.click(screen.getByRole('button', { name: 'Plain' }))
  await waitFor(() => expect(box).not.toBeChecked())
  expect(box).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: en.newChat }))
  await waitFor(() => expect(box).toBeEnabled())
})

test('a reloaded paused team chat shows the question as pending and the reply goes to team with the switch off', async () => {
  localStorage.setItem('conversation_auto_u1', 't')
  localStorage.setItem('team_mode', '0')
  const calls = installFetch({
    'GET /api/conversations': () => json([{ id: 't', title: 'Group', kind: 'team' }]),
    'GET /api/conversations/t/messages': () =>
      json([
        { role: 'user', content: 'Invest?' },
        { role: 'assistant', agent: 'orchestrator', content: 'How old are you?', pending: true, team: { participants: ['sql'], tasks: [] } },
      ]),
    'POST /api/chat/team': () => sseResponse([{ type: 'token', text: 'Thanks' }, { type: 'done' }]),
  })
  renderApp()
  expect(await screen.findByText('How old are you?')).toBeInTheDocument()
  expect(screen.getByText(en.waitingForYou)).toBeInTheDocument()
  await userEvent.type(screen.getByPlaceholderText(en.answerPlaceholder), '30{Enter}')
  await screen.findByText('Thanks')
  expect(posts(calls)).toEqual(['/api/chat/team'])
  expect(JSON.parse(calls.find((c) => c.method === 'POST')!.init!.body as string)).toEqual({
    message: '30',
    conversation_id: 't',
  })
  expect(screen.queryByText(en.waitingForYou)).toBeNull()
  expect(screen.getByPlaceholderText(en.placeholder)).toBeInTheDocument()
})

test('a paused new team chat is answered through the team endpoint', async () => {
  const calls = installFetch({
    'POST /api/chat/team': (init) =>
      sseResponse(
        JSON.parse(init!.body as string).conversation_id
          ? [{ type: 'token', text: 'resumed' }, { type: 'done' }]
          : [{ type: 'conversation', id: 'n' }, { type: 'needs_human', question: 'Which?' }, { type: 'done' }],
      ),
  })
  localStorage.setItem('team_mode', '1')
  renderApp()
  await userEvent.type(await screen.findByPlaceholderText(en.placeholder), 'q{Enter}')
  await userEvent.type(await screen.findByPlaceholderText(en.answerPlaceholder), 'a{Enter}')
  await screen.findByText('resumed')
  expect(posts(calls)).toEqual(['/api/chat/team', '/api/chat/team'])
})
