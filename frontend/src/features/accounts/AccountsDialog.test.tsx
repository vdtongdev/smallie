import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { AuthProvider } from '@/features/auth/AuthProvider'
import { LanguageProvider } from '@/i18n'
import { en } from '@/i18n/en'
import { installFetch, json, me } from '@/test/chatMock'
import AccountsDialog from './AccountsDialog'

const accounts = [
  { id: 'a0', name: 'Boss', username: 'boss', is_admin: true },
  { id: 'a1', name: 'Binh', username: 'binh', is_admin: false },
  { id: 'a2', name: '', username: 'chi', is_admin: false },
]

type Routes = Parameters<typeof installFetch>[0]

function setup(routes: Routes = {}) {
  const calls = installFetch({
    'GET /api/me': () => json({ ...me, is_admin: true, max_accounts: 5 }),
    'GET /api/admin/accounts': () => json(accounts),
    ...routes,
  })
  render(
    <LanguageProvider>
      <AuthProvider>
        <AccountsDialog open onOpenChange={() => {}} />
      </AuthProvider>
    </LanguageProvider>,
  )
  return calls
}

const row = (text: string) => screen.getByText(text, { exact: false }).closest('li') as HTMLElement

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('lang', 'en')
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

test('shows the count of non-admin accounts out of the maximum, and one row per account', async () => {
  setup()
  expect(await screen.findByText(en.accountCount.replace('{count}', '2').replace('{max}', '5'))).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: en.accountsTitle })).toBeInTheDocument()
  expect(screen.getByText(`Boss (@boss) · ${en.adminBadge}`)).toBeInTheDocument()
  expect(screen.getByText('Binh (@binh)')).toBeInTheDocument()
  expect(screen.getByText('chi (@chi)')).toBeInTheDocument() // no name: the username stands in
})

test('the admin row has no reset or delete actions; the others do', async () => {
  setup()
  await screen.findByText('Binh (@binh)')
  expect(within(row('Boss (@boss)')).queryAllByRole('button')).toHaveLength(0)
  const binh = within(row('Binh (@binh)'))
  expect(binh.getByRole('button', { name: en.resetPassword })).toBeInTheDocument()
  expect(binh.getByRole('button', { name: en.delete })).toBeInTheDocument()
})

test('reset with a short password shows the hint and makes no request', async () => {
  const calls = setup()
  await screen.findByText('Binh (@binh)')
  await userEvent.click(within(row('Binh (@binh)')).getByRole('button', { name: en.resetPassword }))
  await userEvent.type(screen.getByLabelText(en.newPassword.replace('{user}', 'binh')), 'short')
  await userEvent.click(screen.getByRole('button', { name: en.save }))
  expect(await screen.findByText(en.passwordHint)).toBeInTheDocument()
  expect(calls.filter((c) => c.method === 'POST' && c.url.includes('/password'))).toHaveLength(0)
})

test('reset success posts the password and shows the message', async () => {
  const calls = setup({ 'POST /api/admin/accounts/a1/password': () => json({}) })
  await screen.findByText('Binh (@binh)')
  await userEvent.click(within(row('Binh (@binh)')).getByRole('button', { name: en.resetPassword }))
  await userEvent.type(screen.getByLabelText(en.newPassword.replace('{user}', 'binh')), 'longenough1')
  await userEvent.click(screen.getByRole('button', { name: en.save }))
  expect(await screen.findByText(en.passwordChanged)).toBeInTheDocument()
  const post = calls.find((c) => c.method === 'POST' && c.url === '/api/admin/accounts/a1/password')!
  expect(JSON.parse(post.init!.body as string)).toEqual({ password: 'longenough1' })
})

test('a failed reset shows the error', async () => {
  setup({ 'POST /api/admin/accounts/a1/password': () => json({ detail: 'nope' }, 400) })
  await screen.findByText('Binh (@binh)')
  await userEvent.click(within(row('Binh (@binh)')).getByRole('button', { name: en.resetPassword }))
  await userEvent.type(screen.getByLabelText(en.newPassword.replace('{user}', 'binh')), 'longenough1')
  await userEvent.click(screen.getByRole('button', { name: en.save }))
  expect(await screen.findByText('nope')).toBeInTheDocument()
})

test('delete asks first; cancel makes no request', async () => {
  const calls = setup()
  await screen.findByText('Binh (@binh)')
  await userEvent.click(within(row('Binh (@binh)')).getByRole('button', { name: en.delete }))
  expect(await screen.findByText(en.confirmDeleteAccount.replace('{user}', 'binh'))).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: en.cancel }))
  expect(calls.filter((c) => c.method === 'DELETE')).toHaveLength(0)
})

test('delete confirm sends DELETE and reloads the list', async () => {
  let list = accounts
  const calls = setup({
    'GET /api/admin/accounts': () => json(list),
    'DELETE /api/admin/accounts/a1': () => {
      list = accounts.filter((a) => a.id !== 'a1')
      return json({})
    },
  })
  await screen.findByText('Binh (@binh)')
  await userEvent.click(within(row('Binh (@binh)')).getByRole('button', { name: en.delete }))
  const confirm = await screen.findByText(en.confirmDeleteAccount.replace('{user}', 'binh'))
  await userEvent.click(within(confirm.closest('[role="alertdialog"]') as HTMLElement).getByRole('button', { name: en.delete }))
  await waitFor(() => expect(screen.queryByText('Binh (@binh)')).toBeNull())
  expect(calls.filter((c) => c.method === 'DELETE')).toHaveLength(1)
})

test('a load error shows in the dialog', async () => {
  setup({ 'GET /api/admin/accounts': () => json({ detail: 'admin only' }, 403) })
  expect(await screen.findByRole('alert')).toHaveTextContent('admin only')
})

const posts = (calls: ReturnType<typeof installFetch>) => calls.filter((c) => c.method === 'POST' && c.url.includes('/password'))

async function typeNewPassword(value: string) {
  await screen.findByText('Binh (@binh)')
  await userEvent.click(within(row('Binh (@binh)')).getByRole('button', { name: en.resetPassword }))
  await userEvent.type(screen.getByLabelText(en.newPassword.replace('{user}', 'binh')), value)
  await userEvent.click(screen.getByRole('button', { name: en.save }))
}

test('a 7-character password shows the hint and sends nothing', async () => {
  const calls = setup({ 'POST /api/admin/accounts/a1/password': () => json({}) })
  await typeNewPassword('1234567')
  expect(await screen.findByText(en.passwordHint)).toBeInTheDocument()
  expect(posts(calls)).toHaveLength(0)
})

test('an 8-character password is posted', async () => {
  const calls = setup({ 'POST /api/admin/accounts/a1/password': () => json({}) })
  await typeNewPassword('12345678')
  expect(await screen.findByText(en.passwordChanged)).toBeInTheDocument()
  expect(JSON.parse(posts(calls)[0].init!.body as string)).toEqual({ password: '12345678' })
})

test('a failed delete keeps its error visible after the list reloads', async () => {
  const calls = setup({ 'DELETE /api/admin/accounts/a1': () => json({ detail: 'cannot delete' }, 500) })
  await screen.findByText('Binh (@binh)')
  await userEvent.click(within(row('Binh (@binh)')).getByRole('button', { name: en.delete }))
  const confirm = await screen.findByText(en.confirmDeleteAccount.replace('{user}', 'binh'))
  await userEvent.click(within(confirm.closest('[role="alertdialog"]') as HTMLElement).getByRole('button', { name: en.delete }))
  await waitFor(() => expect(calls.filter((c) => c.url === '/api/admin/accounts' && c.method === 'GET')).toHaveLength(2))
  expect(await screen.findByRole('alert')).toHaveTextContent('cannot delete')
})

test('no window.prompt, confirm or alert is used', async () => {
  const spies = (['prompt', 'confirm', 'alert'] as const).map((k) => vi.spyOn(window, k).mockReturnValue(null as never))
  setup()
  await screen.findByText('Binh (@binh)')
  await userEvent.click(within(row('Binh (@binh)')).getByRole('button', { name: en.delete }))
  await userEvent.click(await screen.findByRole('button', { name: en.cancel }))
  await userEvent.click(within(row('Binh (@binh)')).getByRole('button', { name: en.resetPassword }))
  spies.forEach((s) => expect(s).not.toHaveBeenCalled())
})
