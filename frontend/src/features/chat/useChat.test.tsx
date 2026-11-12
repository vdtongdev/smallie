import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { AuthProvider } from '@/features/auth/AuthProvider'
import { LanguageProvider } from '@/i18n'
import { en } from '@/i18n/en'
import { installFetch, json, sseResponse } from '@/test/chatMock'
import { useChat } from './useChat'

const wrapper = ({ children }: { children: ReactNode }) => (
  <LanguageProvider>
    <AuthProvider>{children}</AuthProvider>
  </LanguageProvider>
)
const setup = async () => {
  const hook = renderHook(() => useChat(), { wrapper })
  await waitFor(() => expect(hook.result.current.ready).toBe(true))
  return hook
}
const texts = (turn: { blocks: { kind: string; text?: string }[] }) =>
  turn.blocks.filter((b) => b.kind === 'text').map((b) => b.text)
const gate = () => {
  let release!: () => void
  const wait = new Promise<void>((r) => (release = r))
  return { wait, release }
}
const emptyUsage = { reply: null, chat: { input_tokens: 0, output_tokens: 0, cached_tokens: 0 } }

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

test('send streams tokens into one turn and remembers the conversation', async () => {
  const calls = installFetch({
    'POST /api/chat/auto': () =>
      sseResponse([
        { type: 'conversation', id: 'c9' },
        { type: 'token', text: 'Hel' },
        { type: 'token', text: 'lo' },
        { type: 'usage', reply: null, chat: { input_tokens: 1, output_tokens: 2, cached_tokens: 0 } },
        { type: 'done' },
      ]),
  })
  const { result } = await setup()
  await act(() => result.current.send('hi'))
  expect(result.current.turns).toHaveLength(1)
  expect(result.current.turns[0].user).toBe('hi')
  expect(texts(result.current.turns[0])).toEqual(['Hello'])
  expect(result.current.turns[0].running).toBe(false)
  expect(result.current.currentId).toBe('c9')
  expect(localStorage.getItem('conversation_auto_u1')).toBe('c9')
  expect(result.current.usage?.chat.output_tokens).toBe(2)
  expect(result.current.busy).toBe(false)
  const post = calls.find((c) => c.method === 'POST')!
  expect(JSON.parse(post.init!.body as string)).toEqual({ message: 'hi', conversation_id: null })
})

test('blank text is not sent', async () => {
  const calls = installFetch()
  const { result } = await setup()
  await act(() => result.current.send('   '))
  expect(calls.some((c) => c.method === 'POST')).toBe(false)
  expect(result.current.turns).toEqual([])
})

test('while busy a second send and newChat do nothing', async () => {
  const g = gate()
  const calls = installFetch({
    'POST /api/chat/auto': async () => {
      await g.wait
      return sseResponse([{ type: 'conversation', id: 'c1' }, { type: 'token', text: 'a' }, { type: 'done' }])
    },
  })
  const { result } = await setup()
  let first!: Promise<void>
  act(() => {
    first = result.current.send('one')
  })
  await waitFor(() => expect(result.current.busy).toBe(true))
  await act(() => result.current.send('two'))
  act(() => result.current.newChat())
  expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1)
  expect(result.current.turns).toHaveLength(1)
  g.release()
  await act(() => first)
  expect(result.current.turns[0].user).toBe('one')
  expect(result.current.busy).toBe(false)
})

test('a dropped connection mid-stream keeps the text, adds the error and frees the composer', async () => {
  installFetch({
    'POST /api/chat/auto': () =>
      sseResponse([{ type: 'token', text: 'partial ' }, { type: 'token', text: 'answer' }], new Error('network down')),
  })
  const { result } = await setup()
  await act(() => result.current.send('hi'))
  const turn = result.current.turns[0]
  expect(texts(turn)).toEqual(['partial answer'])
  expect(turn.blocks.at(-1)).toMatchObject({ kind: 'error', message: 'network down' })
  expect(turn.running).toBe(false)
  expect(result.current.busy).toBe(false)
})

test('a non-OK answer ends the turn with the server detail', async () => {
  installFetch({ 'POST /api/chat/auto': () => json({ detail: 'Too many requests' }, 429) })
  const { result } = await setup()
  await act(() => result.current.send('hi'))
  expect(result.current.turns[0].blocks).toEqual([{ kind: 'error', message: 'Too many requests' }])
  expect(result.current.busy).toBe(false)
})

test('a stream that ends without done is marked cut', async () => {
  installFetch({ 'POST /api/chat/auto': () => sseResponse([{ type: 'token', text: 'half' }]) })
  const { result } = await setup()
  await act(() => result.current.send('hi'))
  expect(result.current.turns[0].blocks.at(-1)).toMatchObject({ kind: 'error', code: 'stream_cut' })
})

test('opening A then B quickly never shows A in B', async () => {
  const gA = gate()
  const msg = (text: string) => [
    { role: 'user', content: text },
    { role: 'assistant', content: `re ${text}` },
  ]
  installFetch({
    'GET /api/conversations': () =>
      json([
        { id: 'A', title: 'a', kind: 'auto' },
        { id: 'B', title: 'b', kind: 'auto' },
      ]),
    'GET /api/conversations/A/messages': async () => {
      await gA.wait
      return json(msg('from A'))
    },
    'GET /api/conversations/B/messages': () => json(msg('from B')),
    'GET /api/conversations/A/usage': () => json({ reply: null, chat: { input_tokens: 1, output_tokens: 1, cached_tokens: 0 } }),
    'GET /api/conversations/B/usage': () => json({ reply: null, chat: { input_tokens: 9, output_tokens: 9, cached_tokens: 0 } }),
  })
  const { result } = await setup()
  let a!: Promise<void>
  act(() => {
    a = result.current.open('A')
  })
  await act(() => result.current.open('B'))
  expect(result.current.turns[0].user).toBe('from B')
  gA.release()
  await act(() => a)
  expect(result.current.currentId).toBe('B')
  expect(result.current.turns.map((t) => t.user)).toEqual(['from B'])
  expect(result.current.usage?.chat.input_tokens).toBe(9)
})

test('a stored conversation that no longer exists starts a new chat without an error', async () => {
  localStorage.setItem('conversation_auto_u1', 'gone')
  installFetch({ 'GET /api/conversations/gone/messages': () => json({ detail: 'Conversation not found' }, 404) })
  const { result } = await setup()
  await waitFor(() => expect(localStorage.getItem('conversation_auto_u1')).toBe(''))
  expect(result.current.currentId).toBe(null)
  expect(result.current.turns).toEqual([])
  expect(result.current.loadError).toBe(null)
})

test('another load error shows one error line', async () => {
  installFetch({
    'GET /api/conversations': () => json([{ id: 'A', title: 'a', kind: 'auto' }]),
    'GET /api/conversations/A/messages': () => json({ detail: 'boom' }, 500),
  })
  const { result } = await setup()
  await act(() => result.current.open('A'))
  expect(result.current.loadError).toBe(`${en.loadFailed}: boom`)
  expect(result.current.turns).toEqual([])
})

test('an opened team conversation posts to the team endpoint', async () => {
  const calls = installFetch({
    'GET /api/conversations': () => json([{ id: 'T', title: 't', kind: 'team' }]),
    'GET /api/conversations/T/messages': () => json([]),
    'GET /api/conversations/T/usage': () => json(emptyUsage),
    'POST /api/chat/team': () => sseResponse([{ type: 'done' }]),
  })
  const { result } = await setup()
  await act(() => result.current.open('T'))
  await act(() => result.current.send('go'))
  const post = calls.find((c) => c.method === 'POST')!
  expect(post.url).toBe('/api/chat/team')
  expect(JSON.parse(post.init!.body as string).conversation_id).toBe('T')
})

test('removing the current chat starts a new one and reloads the list', async () => {
  let list = [{ id: 'A', title: 'a', kind: 'auto' }]
  const calls = installFetch({
    'GET /api/conversations': () => json(list),
    'GET /api/conversations/A/messages': () => json([{ role: 'user', content: 'x' }]),
    'GET /api/conversations/A/usage': () => json(emptyUsage),
    'DELETE /api/conversations/A': () => {
      list = []
      return new Response(null, { status: 204 })
    },
  })
  const { result } = await setup()
  await act(() => result.current.open('A'))
  expect(result.current.currentId).toBe('A')
  await act(() => result.current.remove('A'))
  expect(calls.some((c) => c.method === 'DELETE' && c.url === '/api/conversations/A')).toBe(true)
  expect(result.current.currentId).toBe(null)
  expect(result.current.turns).toEqual([])
  expect(result.current.conversations).toEqual([])
})

test('the remembered conversation reopens after login', async () => {
  localStorage.setItem('conversation_auto_u1', 'A')
  installFetch({
    'GET /api/conversations': () => json([{ id: 'A', title: 'a', kind: 'auto' }]),
    'GET /api/conversations/A/messages': () => json([{ role: 'user', content: 'earlier' }]),
    'GET /api/conversations/A/usage': () => json(emptyUsage),
  })
  const { result } = await setup()
  await waitFor(() => expect(result.current.turns.map((t) => t.user)).toEqual(['earlier']))
  expect(result.current.currentId).toBe('A')
})

test('open does nothing while a reply streams', async () => {
  const g = gate()
  const calls = installFetch({
    'GET /api/conversations': () => json([{ id: 'A', title: 'a', kind: 'auto' }]),
    'POST /api/chat/auto': async () => {
      await g.wait
      return sseResponse([{ type: 'conversation', id: 'c1' }, { type: 'done' }])
    },
  })
  const { result } = await setup()
  let first!: Promise<void>
  act(() => {
    first = result.current.send('one')
  })
  await waitFor(() => expect(result.current.busy).toBe(true))
  await act(() => result.current.open('A'))
  expect(calls.some((c) => c.url === '/api/conversations/A/messages')).toBe(false)
  expect(result.current.currentId).toBe(null)
  g.release()
  await act(() => first)
})
