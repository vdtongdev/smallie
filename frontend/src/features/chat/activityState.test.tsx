import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { LanguageProvider } from '@/i18n'
import { en } from '@/i18n/en'
import { newTurn, type Turn } from '@/lib/turn'
import { installFetch } from '@/test/chatMock'
import ChatView from './ChatView'
import type { Chat } from './useChat'

beforeEach(() => installFetch())
afterEach(() => vi.unstubAllGlobals())

const turnWith = (tool: string): Turn => {
  const t = newTurn(`q ${tool}`)
  t.running = false
  t.finished = true
  t.activity.tools.push({ name: tool, args: { query: tool }, result: null })
  return t
}
const chat = (currentId: string | null, turns: Turn[]) =>
  ({ conversations: [], currentId, turns, busy: false, usage: null, loadError: null, ready: true }) as unknown as Chat
const view = (c: Chat) => (
  <LanguageProvider>
    <ChatView chat={c} draft={null} onOpenSettings={() => {}} />
  </LanguageProvider>
)
const toolSummary = (name: string) => screen.queryByText(new RegExp(`${en.usedTool}: ${name}`))

test('the Activity dialog closes when another chat replaces the turns', async () => {
  const { rerender } = render(view(chat('A', [turnWith('a_one'), turnWith('a_two')])))
  await userEvent.click(screen.getAllByRole('button', { name: `${en.activity} (1)` })[1])
  expect(toolSummary('a_two')).toBeInTheDocument()
  rerender(view(chat('B', [turnWith('b_one'), turnWith('b_two')])))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(toolSummary('b_two')).toBeNull()
})

test('the Activity dialog does not come back when the list shrinks and grows again', async () => {
  const { rerender } = render(view(chat('A', [turnWith('a_one'), turnWith('a_two')])))
  await userEvent.click(screen.getAllByRole('button', { name: `${en.activity} (1)` })[1])
  rerender(view(chat('A', [turnWith('a_one')])))
  expect(screen.queryByRole('dialog')).toBeNull()
  rerender(view(chat('A', [turnWith('a_one'), turnWith('a_two')])))
  expect(screen.queryByRole('dialog')).toBeNull()
})
