import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import { LanguageProvider } from '@/i18n'
import { en } from '@/i18n/en'
import type { Conversation } from '@/lib/types'
import Sidebar from './Sidebar'

const items: Conversation[] = [
  { id: 'a', title: 'First chat', kind: 'auto' },
  { id: 't', title: 'Team chat', kind: 'team' },
]

function show(over: Partial<Parameters<typeof Sidebar>[0]> = {}) {
  const props = {
    conversations: items,
    currentId: 'a',
    busy: false,
    teamMode: false,
    onTeamMode: vi.fn(),
    onNew: vi.fn(),
    onOpen: vi.fn(),
    onRemove: vi.fn(),
    ...over,
  }
  render(
    <LanguageProvider>
      <Sidebar {...props} />
    </LanguageProvider>,
  )
  return props
}

test('lists titles, marks the active chat and team chats', () => {
  show()
  expect(screen.getByText('First chat')).toBeInTheDocument()
  expect(screen.getByText('Team chat')).toBeInTheDocument()
  expect(screen.getByText('First chat').closest('li')).toHaveAttribute('aria-current', 'true')
  expect(screen.getByText('Team chat').closest('li')).not.toHaveAttribute('aria-current')
  const marker = screen.getByTitle(en.teamMode)
  expect(marker).toHaveTextContent('👥')
  expect(screen.getByText('First chat').closest('li')!.querySelector(`[title="${en.teamMode}"]`)).toBeNull()
})

test('clicking a title opens that chat', async () => {
  const p = show()
  await userEvent.click(screen.getByText('Team chat'))
  expect(p.onOpen).toHaveBeenCalledWith('t')
})

test('delete asks first: cancel keeps it, confirm removes it', async () => {
  const p = show()
  await userEvent.click(screen.getByRole('button', { name: `${en.delete} First chat` }))
  expect(await screen.findByText(en.confirmDelete)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: en.cancel }))
  expect(p.onRemove).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: `${en.delete} First chat` }))
  await userEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: en.delete }))
  expect(p.onRemove).toHaveBeenCalledWith('a')
})

test('New chat starts a new chat', async () => {
  const p = show()
  await userEvent.click(screen.getByRole('button', { name: en.newChat }))
  expect(p.onNew).toHaveBeenCalledTimes(1)
})

test('New chat and the chat titles are disabled while a reply streams', () => {
  show({ busy: true })
  expect(screen.getByRole('button', { name: en.newChat })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Team chat' })).toBeDisabled()
})

test('the Team mode switch reports changes when no chat is open', async () => {
  const p = show({ currentId: null })
  const box = screen.getByRole('checkbox', { name: en.teamMode })
  expect(box).toBeEnabled()
  await userEvent.click(box)
  expect(p.onTeamMode).toHaveBeenCalledWith(true)
})

test('the Team mode switch is disabled with an open chat and shows its state', () => {
  show({ currentId: 'a', teamMode: true })
  const box = screen.getByRole('checkbox', { name: en.teamMode })
  expect(box).toBeDisabled()
  expect(box).toBeChecked()
})

test('the Team mode switch is disabled while busy', () => {
  show({ currentId: null, busy: true })
  expect(screen.getByRole('checkbox', { name: en.teamMode })).toBeDisabled()
})

test('an empty list shows the empty text', () => {
  show({ conversations: [], currentId: null })
  expect(screen.getByText(en.noConversations)).toBeInTheDocument()
})
