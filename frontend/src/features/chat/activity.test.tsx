import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '@/App'
import { AuthProvider } from '@/features/auth/AuthProvider'
import { LanguageProvider } from '@/i18n'
import { en } from '@/i18n/en'
import { newTurn, type Turn } from '@/lib/turn'
import { installFetch, json, sseResponse } from '@/test/chatMock'
import ActivityDialog from './ActivityDialog'
import { activityCount } from './StatusLine'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const turnWith = (act: Partial<Turn['activity']>): Turn => {
  const turn = newTurn('q')
  turn.activity = { ...turn.activity, ...act }
  return turn
}
const wrap = (turn: Turn) => (
  <LanguageProvider>
    <ActivityDialog turn={turn} open onOpenChange={() => {}} />
  </LanguageProvider>
)
const show = (turn: Turn) => render(wrap(turn))

const routing = { type: 'routing' as const, difficulty: 'easy', profile: 'balanced', tier: 'small', model: 'm-small', reason: 'simple question' }

describe('ActivityDialog', () => {
  it('shows the routing model line and the model with its reason', () => {
    show(turnWith({ routing }))
    expect(screen.getByText('easy (1v1) · Balanced → small model')).toBeInTheDocument()
    expect(screen.getByText('m-small — simple question')).toBeInTheDocument()
  })

  it('shows the routing fallback with a target model', () => {
    show(turnWith({ routing: { ...routing, fallback: { from_model: 'm-small', to_model: 'm-std', category: 'server_error' } } as never }))
    expect(screen.getByText('small model m-small failed (server error), repeated on m-std')).toBeInTheDocument()
  })

  it('shows turn fallbacks with and without to_model', () => {
    show(
      turnWith({
        fallbacks: [
          { type: 'model_fallback', from_model: 'a', to_model: 'b', category: 'unsupported' },
          { type: 'model_fallback', from_model: 'c', category: 'odd_reason' },
        ],
      }),
    )
    expect(screen.getByText('a failed (not supported), repeated on b')).toBeInTheDocument()
    expect(screen.getByText('c failed (odd_reason), used standard')).toBeInTheDocument()
  })

  it('shows a tool as a summary cut to 120 characters, its args and its result', () => {
    const long = 'x'.repeat(200)
    show(
      turnWith({
        tools: [
          { name: 'run_sql', args: { sql: long }, result: '3 rows' },
          { name: 'note', args: { fact: 'f1', other: 1 }, result: null },
        ],
      }),
    )
    expect(screen.getByText(`${en.usedTool}: run_sql — ${'x'.repeat(120)}`)).toBeInTheDocument()
    expect([...document.querySelectorAll('pre')].map((p) => p.textContent)).toContain(JSON.stringify({ sql: long }, null, 2))
    expect(screen.getByText(`${en.result}: run_sql`)).toBeInTheDocument()
    expect(screen.getByText('3 rows')).toBeInTheDocument()
    expect(screen.getByText(`${en.usedTool}: note — f1`)).toBeInTheDocument()
    expect(screen.queryByText(`${en.result}: note`)).not.toBeInTheDocument()
  })

  it('shows a query string as the body, not JSON', () => {
    show(turnWith({ tools: [{ name: 'search', args: { query: 'cats' }, result: null }] }))
    expect(screen.getByText(`${en.usedTool}: search — cats`)).toBeInTheDocument()
    const pre = document.querySelector('pre')!
    expect(pre.textContent).toBe('cats')
  })

  it('shows an asked agent with its text as markdown and its tools', () => {
    show(
      turnWith({
        asked: [{ agent: 'docs', text: 'some **bold** words', tools: [{ name: 'find', args: { query: 'q' }, result: 'r' }] }],
      }),
    )
    expect(screen.getByText('docs')).toBeInTheDocument()
    expect(screen.getByText('bold').tagName).toBe('STRONG')
    expect(screen.getByText(`${en.usedTool}: find — q`)).toBeInTheDocument()
  })

  it('shows the question section, and no "other steps" heading without waves', () => {
    show(turnWith({ question: 'Which year?', tools: [{ name: 't', args: {}, result: null }] }))
    expect(screen.getByRole('heading', { name: en.yourQuestion })).toBeInTheDocument()
    expect(screen.getByText('Which year?')).toBeInTheDocument()
    expect(screen.queryByText(en.otherSteps)).not.toBeInTheDocument()
  })

  it('follows the turn when it changes', () => {
    const { rerender } = show(turnWith({}))
    expect(screen.queryByText(`${en.usedTool}: t — a`)).not.toBeInTheDocument()
    rerender(wrap(turnWith({ tools: [{ name: 't', args: { query: 'a' }, result: null }] })))
    expect(screen.getByText(`${en.usedTool}: t — a`)).toBeInTheDocument()
  })

  it('count equals the steps the dialog shows', () => {
    const turn = turnWith({
      routing: { ...routing, fallback: { from_model: 'a', category: 'x' } } as never,
      fallbacks: [{ type: 'model_fallback', from_model: 'a', category: 'x' }],
      tools: [{ name: 't', args: {}, result: null }],
      asked: [{ agent: 'docs', text: '', tools: [{ name: 'u', args: {}, result: null }] }],
      question: 'q?',
    })
    // routing 1 + fallbacks 1 + tools 1 + asked (1 + 1) + question 1
    expect(activityCount(turn)).toBe(6)
  })
})

describe('Activity button in the chat', () => {
  it('opens the dialog for that turn and gives focus back to the button on close', async () => {
    installFetch({
      'GET /api/agents': () => json([]),
      'POST /api/chat/auto': () =>
        sseResponse([
          { type: 'tool_call', name: 'run_sql', args: { sql: 'select 1' } },
          { type: 'tool_result', name: 'run_sql', content: 'one row' },
          { type: 'token', text: 'ok' },
          { type: 'done' },
        ]),
    })
    render(
      <LanguageProvider>
        <AuthProvider>
          <App />
        </AuthProvider>
      </LanguageProvider>,
    )
    await userEvent.type(await screen.findByPlaceholderText(en.placeholder), 'q{Enter}')
    const button = await screen.findByRole('button', { name: `${en.activity} (1)` })
    await userEvent.click(button)
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('one row')).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(button).toHaveFocus())
  })
})
