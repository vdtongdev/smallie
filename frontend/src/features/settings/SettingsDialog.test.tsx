import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { LanguageProvider } from '@/i18n'
import { en } from '@/i18n/en'
import { cachedModels } from '@/lib/modelSettings'
import { installFetch, json } from '@/test/chatMock'
import SettingsDialog from './SettingsDialog'

const A = 'https://api.a.test/v1'
const B = 'https://api.b.test/v1'
const saved = { base_url: A, model: 'm1', small_model: '', strong_model: '', profile: 'balanced', key_hint: 'sk-...wxyz' }

type Routes = Parameters<typeof installFetch>[0]

function setup(routes: Routes = {}, open = true) {
  const onOpenChange = vi.fn()
  const calls = installFetch({ 'GET /api/model-settings': () => json(saved), ...routes })
  render(
    <LanguageProvider>
      <SettingsDialog open={open} onOpenChange={onOpenChange} />
    </LanguageProvider>,
  )
  return { calls, onOpenChange }
}

type Calls = ReturnType<typeof installFetch>
const puts = (calls: Calls) => calls.filter((c) => c.method === 'PUT')
const putBody = (calls: Calls) => JSON.parse(puts(calls)[0].init!.body as string)
const loaded = () => screen.findByText(en.settingsKeySaved)
const cache = (url: string, models: string[]) => localStorage.setItem(`models:${url}`, JSON.stringify({ models, fetched_at: 1 }))

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('lang', 'en')
  document.documentElement.dataset.theme = 'chatroom'
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  delete document.documentElement.dataset.theme
  document.documentElement.classList.remove('dark')
})

test('loads the saved values; the key field is empty with the hint as placeholder', async () => {
  setup()
  await loaded()
  const key = screen.getByLabelText(en.apiKey) as HTMLInputElement
  expect(key.value).toBe('')
  expect(key.placeholder).toBe('sk-...wxyz')
  expect(key.type).toBe('password')
  expect((screen.getByLabelText(en.apiEndpoint) as HTMLInputElement).value).toBe(A)
  expect((screen.getByLabelText(en.standardModel) as HTMLInputElement).value).toBe('m1')
  expect(screen.getByRole('radio', { name: new RegExp(en.balancedProfile) })).toBeChecked()
})

test('404 means nothing saved', async () => {
  setup({ 'GET /api/model-settings': () => json({ detail: 'No model settings' }, 404) })
  expect(await screen.findByText(en.settingsNone)).toBeInTheDocument()
  expect((screen.getByLabelText(en.apiEndpoint) as HTMLInputElement).value).toBe('')
})

test('another load error shows in the status line', async () => {
  setup({ 'GET /api/model-settings': () => json({ detail: 'database down' }, 500) })
  expect(await screen.findByText('database down')).toBeInTheDocument()
})

test('saving with an empty key field sends api_key null, never an empty string', async () => {
  const { calls, onOpenChange } = setup({ 'PUT /api/model-settings': () => json({}) })
  await loaded()
  const model = screen.getByLabelText(en.standardModel)
  await userEvent.clear(model)
  await userEvent.type(model, 'm2')
  await userEvent.click(screen.getByRole('button', { name: en.save }))
  await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
  expect(puts(calls)).toHaveLength(1)
  expect(putBody(calls)).toEqual({ base_url: A, model: 'm2', small_model: '', strong_model: '', profile: 'balanced', api_key: null })
  expect(putBody(calls).api_key).toBeNull()
})

test('a typed key is sent trimmed', async () => {
  const { calls } = setup({ 'PUT /api/model-settings': () => json({}) })
  await loaded()
  await userEvent.type(screen.getByLabelText(en.apiKey), '  sk-new  ')
  await userEvent.click(screen.getByRole('button', { name: en.save }))
  await waitFor(() => expect(puts(calls)).toHaveLength(1))
  expect(putBody(calls).api_key).toBe('sk-new')
})

test('saving with nothing changed closes without a PUT', async () => {
  const { calls, onOpenChange } = setup()
  await loaded()
  await userEvent.click(screen.getByRole('button', { name: en.save }))
  expect(onOpenChange).toHaveBeenCalledWith(false)
  expect(puts(calls)).toHaveLength(0)
})

test('a changed profile is a change', async () => {
  const { calls } = setup({ 'PUT /api/model-settings': () => json({}) })
  await loaded()
  await userEvent.click(screen.getByRole('radio', { name: new RegExp(en.economyProfile) }))
  await userEvent.click(screen.getByRole('button', { name: en.save }))
  await waitFor(() => expect(puts(calls)).toHaveLength(1))
  expect(putBody(calls).profile).toBe('economy')
})

test('a save with a slot warning stays open and shows it under the slot', async () => {
  cache(A, ['m1', 's1'])
  const { calls, onOpenChange } = setup({
    'PUT /api/model-settings': () => json({ warnings: { small_model: 'Small slot warning' } }),
  })
  await loaded()
  const small = screen.getByLabelText(en.smallModel)
  await userEvent.selectOptions(small, 's1')
  await userEvent.click(screen.getByRole('button', { name: en.save }))
  const warning = await screen.findByText('Small slot warning')
  expect(small.nextElementSibling).toBe(warning)
  expect(screen.getByText(new RegExp(`^${en.saved}\\.`))).toBeInTheDocument()
  expect(putBody(calls).small_model).toBe('s1')
  expect(onOpenChange).not.toHaveBeenCalledWith(false)
})

test('the plain warning shows under the standard model', async () => {
  const { onOpenChange } = setup({ 'PUT /api/model-settings': () => json({ warning: 'Model may not exist' }) })
  await loaded()
  const model = screen.getByLabelText(en.standardModel)
  await userEvent.type(model, 'x')
  await userEvent.click(screen.getByRole('button', { name: en.save }))
  const warning = await screen.findByText('Model may not exist')
  expect(model.nextElementSibling).toBe(warning)
  expect(onOpenChange).not.toHaveBeenCalledWith(false)
})

test('a failed save shows the error and stays open', async () => {
  const { onOpenChange } = setup({ 'PUT /api/model-settings': () => json({ detail: 'The key was rejected' }, 400) })
  await loaded()
  await userEvent.type(screen.getByLabelText(en.apiKey), 'sk-bad')
  await userEvent.click(screen.getByRole('button', { name: en.save }))
  expect(await screen.findByText('The key was rejected')).toBeInTheDocument()
  expect(onOpenChange).not.toHaveBeenCalledWith(false)
})

test('a saved model missing from the provider list still shows, labelled not listed', async () => {
  cache(A, ['a', 'b'])
  setup({ 'GET /api/model-settings': () => json({ ...saved, model: 'old', small_model: 'gone' }) })
  await loaded()
  expect(screen.getAllByRole('option', { name: en.notListed.replace('{id}', 'old') })).toHaveLength(1)
  expect(screen.getAllByRole('option', { name: en.notListed.replace('{id}', 'gone') })).toHaveLength(1)
  expect((screen.getByLabelText(en.standardModel) as HTMLSelectElement).value).toBe('old')
})

test('missing required fields show the message and make no request', async () => {
  const { calls } = setup()
  await loaded()
  await userEvent.clear(screen.getByLabelText(en.apiEndpoint))
  await userEvent.click(screen.getByRole('button', { name: en.save }))
  expect(await screen.findByText(en.settingsFieldsRequired)).toBeInTheDocument()
  expect(puts(calls)).toHaveLength(0)
})

test('with nothing saved a key is required', async () => {
  const { calls } = setup({ 'GET /api/model-settings': () => json({ detail: 'none' }, 404) })
  await screen.findByText(en.settingsNone)
  await userEvent.type(screen.getByLabelText(en.apiEndpoint), A)
  await userEvent.type(screen.getByLabelText(en.standardModel), 'm1')
  await userEvent.click(screen.getByRole('button', { name: en.save }))
  expect(await screen.findByText(en.settingsFieldsRequired)).toBeInTheDocument()
  expect(puts(calls)).toHaveLength(0)
})

test('clear asks first, cancel does nothing, confirm deletes and reloads', async () => {
  const confirm = vi.spyOn(window, 'confirm')
  const prompt = vi.spyOn(window, 'prompt')
  const alert = vi.spyOn(window, 'alert')
  let gone = false
  const { calls } = setup({
    'GET /api/model-settings': () => (gone ? json({ detail: 'none' }, 404) : json(saved)),
    'DELETE /api/model-settings': () => {
      gone = true
      return new Response(null, { status: 204 })
    },
  })
  await loaded()
  await userEvent.click(screen.getByRole('button', { name: en.clear }))
  const ask = await screen.findByRole('alertdialog')
  expect(within(ask).getByText(en.confirmClearSettings)).toBeInTheDocument()
  await userEvent.click(within(ask).getByRole('button', { name: en.cancel }))
  expect(calls.filter((c) => c.method === 'DELETE')).toHaveLength(0)
  await userEvent.click(screen.getByRole('button', { name: en.clear }))
  await userEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: en.clear }))
  expect(await screen.findByText(en.settingsNone)).toBeInTheDocument()
  expect(calls.filter((c) => c.method === 'DELETE')).toHaveLength(1)
  expect(confirm).not.toHaveBeenCalled()
  expect(prompt).not.toHaveBeenCalled()
  expect(alert).not.toHaveBeenCalled()
})

test('without a cached list the standard model is a text input', async () => {
  setup({ 'GET /api/model-settings/models': () => json({ detail: 'no' }, 500) })
  await loaded()
  expect(screen.getByLabelText(en.standardModel).tagName).toBe('INPUT')
  expect(screen.getByLabelText(en.smallModel).tagName).toBe('SELECT')
})

test('with a cached list the standard model is a select', async () => {
  cache(A, ['m1', 'm2'])
  setup()
  await loaded()
  const select = screen.getByLabelText(en.standardModel)
  expect(select.tagName).toBe('SELECT')
  expect(within(select).getAllByRole('option').map((o) => o.textContent)).toEqual(['m1', 'm2'])
  expect(within(screen.getByLabelText(en.smallModel)).getAllByRole('option')[0]).toHaveTextContent(en.sameAsStandard)
})

test('changing the endpoint fetches the provider models and caches them for it', async () => {
  let base = A
  const { calls } = setup({
    'GET /api/model-settings': () => json({ ...saved, base_url: base }),
    'PUT /api/model-settings': (init) => {
      base = JSON.parse(init!.body as string).base_url
      return json({})
    },
    'GET /api/model-settings/models': () => json({ models: ['z', 'y'] }),
  })
  await loaded()
  await waitFor(() => expect(cachedModels(A)).toEqual(['y', 'z']))
  expect(cachedModels(B)).toEqual([])
  const url = screen.getByLabelText(en.apiEndpoint)
  await userEvent.clear(url)
  await userEvent.type(url, B)
  await userEvent.click(screen.getByRole('button', { name: en.save }))
  await waitFor(() => expect(cachedModels(B)).toEqual(['y', 'z']))
  expect(JSON.parse(localStorage.getItem(`models:${B}`)!).models).toEqual(['z', 'y'])
  expect(calls.filter((c) => c.url === '/api/model-settings/models').length).toBeGreaterThanOrEqual(2)
})

test('loads settings and the model list in the background, even while closed', async () => {
  const { calls } = setup({ 'GET /api/model-settings/models': () => json({ models: ['q', 'p'] }) }, false)
  await waitFor(() => expect(cachedModels(A)).toEqual(['p', 'q']))
  expect(calls.map((c) => c.url)).toContain('/api/model-settings')
  expect(screen.queryByRole('dialog')).toBeNull()
})

test('the theme picker lists shadcn, Chatroom and Light and applies a choice at once', async () => {
  setup()
  await loaded()
  const group = screen.getByRole('radiogroup', { name: en.themeGroup })
  const radios = within(group).getAllByRole('radio')
  expect(radios.map((r) => r.closest('label')?.textContent)).toEqual([en.themeShadcn, en.themeChatroom, en.themeLight])
  expect(within(group).getByRole('radio', { name: en.themeChatroom })).toBeChecked()
  await userEvent.click(within(group).getByRole('radio', { name: en.themeLight }))
  expect(document.documentElement.dataset.theme).toBe('light')
  expect(localStorage.getItem('theme')).toBe('light')
  expect(within(group).getByRole('radio', { name: en.themeLight })).toBeChecked()
  await userEvent.click(within(group).getByRole('radio', { name: en.themeShadcn }))
  expect(document.documentElement.dataset.theme).toBe('shadcn')
  expect(localStorage.getItem('theme')).toBe('shadcn')
})

test('choosing Chatroom turns dark on and Light turns it off, whatever the system says', async () => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  document.documentElement.dataset.theme = 'shadcn'
  setup()
  await loaded()
  const group = screen.getByRole('radiogroup', { name: en.themeGroup })
  await userEvent.click(within(group).getByRole('radio', { name: en.themeChatroom }))
  expect(document.documentElement.classList.contains('dark')).toBe(true)
  await userEvent.click(within(group).getByRole('radio', { name: en.themeLight }))
  expect(document.documentElement.classList.contains('dark')).toBe(false)
})

test('with ?theme=smallie no theme radio is checked', async () => {
  document.documentElement.dataset.theme = 'smallie'
  setup()
  await loaded()
  const group = screen.getByRole('radiogroup', { name: en.themeGroup })
  for (const r of within(group).getAllByRole('radio')) expect(r).not.toBeChecked()
})

test('shows the provider table', async () => {
  setup()
  await loaded()
  expect(screen.getByText('https://api.deepseek.com/v1')).toBeInTheDocument()
  expect(screen.getByText('https://dashscope-intl.aliyuncs.com/compatible-mode/v1')).toBeInTheDocument()
  expect(screen.getByText(en.anyOpenAI)).toBeInTheDocument()
})

test('model dropdowns and their options use opaque popover colours in every theme', async () => {
  cache(A, ['m1', 's1'])
  setup()
  await loaded()
  const selects = [en.standardModel, en.smallModel, en.strongModel].map((l) => screen.getByLabelText(l))
  for (const select of selects) {
    expect(select).toHaveClass('bg-popover', 'text-popover-foreground')
    expect(select).not.toHaveClass('bg-transparent')
    const options = select.querySelectorAll('option')
    expect(options.length).toBeGreaterThan(0)
    for (const option of options) expect(option).toHaveClass('bg-popover', 'text-popover-foreground')
  }
})

test('models and profiles help is a closed details with the difficulty and profile explanations', async () => {
  setup()
  await loaded()
  const summary = screen.getByText(en.modelsHelpTitle)
  const details = summary.closest('details')!
  expect(summary.tagName).toBe('SUMMARY')
  expect(details).not.toHaveAttribute('open')
  for (const text of [en.difficultyIntro, en.easyHelp, en.mediumHelp, en.hardHelp, en.helpEconomy, en.helpBalanced, en.helpAdvanced, en.helpOptional, en.helpActivity]) {
    expect(details.textContent).toContain(text)
  }
})

test('the provider table sits in its own closed details', async () => {
  setup()
  await loaded()
  const summary = screen.getByText(en.keyHelpTitle)
  const details = summary.closest('details')!
  expect(details).not.toHaveAttribute('open')
  expect(details).not.toBe(screen.getByText(en.modelsHelpTitle).closest('details'))
  expect(within(details).getByText('platform.deepseek.com')).toBeInTheDocument()
  expect(within(details).getByText(en.anyOpenAI)).toBeInTheDocument()
})
