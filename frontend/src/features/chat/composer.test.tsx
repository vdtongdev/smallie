import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { LanguageProvider } from '@/i18n'
import { en } from '@/i18n/en'
import { installFetch, json } from '@/test/chatMock'
import Composer from './Composer'

test('blank and whitespace input is never passed to onSend', async () => {
  const onSend = vi.fn()
  render(
    <LanguageProvider>
      <Composer busy={false} awaiting={false} draft={null} onSend={onSend} />
    </LanguageProvider>,
  )
  const box = screen.getByPlaceholderText(en.placeholder)
  await userEvent.type(box, '   {Enter}')
  await userEvent.click(screen.getByRole('button', { name: en.send }))
  expect(onSend).not.toHaveBeenCalled()
  await userEvent.type(box, ' hi {Enter}')
  expect(onSend).toHaveBeenCalledWith('hi', [])
})

type D = { id: string; filename: string; status: string; chunk_count: number; error: string | null; images_unread?: number; has_image?: boolean }
const doc = (over: Partial<D> = {}): D => ({ id: 'd1', filename: 'a.png', status: 'ready', chunk_count: 1, error: null, has_image: true, ...over })
const png = (name = 'a.png') => new File(['x'], name, { type: 'image/png' })
let listed: D[]
let uploads: File[][]
let result: D[]
let calls: ReturnType<typeof installFetch>
let revoked: string[]

beforeEach(() => {
  listed = []
  uploads = []
  result = [doc()]
  revoked = []
  let n = 0
  URL.createObjectURL = vi.fn(() => `blob:${++n}`)
  URL.revokeObjectURL = vi.fn((u: string) => void revoked.push(u))
  calls = installFetch({
    'GET /api/documents': () => json(listed),
    'POST /api/documents': (init) => {
      uploads.push((init!.body as FormData).getAll('files') as File[])
      listed = result
      return json(result)
    },
  })
})
afterEach(() => vi.unstubAllGlobals())

const setup = (props: { awaiting?: boolean; busy?: boolean } = {}) => {
  const onSend = vi.fn()
  const view = render(
    <LanguageProvider>
      <Composer busy={props.busy ?? false} awaiting={props.awaiting ?? false} draft={null} onSend={onSend} />
    </LanguageProvider>,
  )
  return { onSend, ...view }
}
const picker = () => document.querySelector('input[type="file"]') as HTMLInputElement
const box = () => screen.getByRole('textbox')
const attach = async (...files: File[]) => {
  await userEvent.upload(picker(), files)
}

test('paperclip picks images and shows a reading chip', async () => {
  result = [doc({ status: 'processing' })]
  setup()
  expect(picker().accept).toBe('.png,.jpg,.jpeg,.webp')
  expect(picker().multiple).toBe(true)
  await attach(png())
  expect(await screen.findByText(en.attachReading)).toBeInTheDocument()
  expect(screen.getByText('a.png')).toBeInTheDocument()
  expect(screen.getByRole('img')).toHaveAttribute('src', 'blob:1')
  expect(uploads).toHaveLength(1)
})

test('pasting an image attaches it', async () => {
  setup()
  fireEvent.paste(box(), { clipboardData: { files: [png('p.png')], items: [], getData: () => '' } })
  await waitFor(() => expect(uploads).toHaveLength(1))
  expect(uploads[0][0].name).toBe('p.png')
})

test('pasting plain text pastes text', async () => {
  setup()
  await userEvent.click(box())
  await userEvent.paste('hello there')
  expect(box()).toHaveValue('hello there')
  expect(uploads).toHaveLength(0)
})

test('dropping an image attaches it', async () => {
  setup()
  fireEvent.drop(box(), { dataTransfer: { files: [png('d.png')], types: ['Files'] } })
  await waitFor(() => expect(uploads).toHaveLength(1))
  expect(uploads[0][0].name).toBe('d.png')
})

test('heic gif bmp are refused with the message', async () => {
  setup()
  for (const name of ['a.heic', 'a.gif', 'a.bmp', 'a.pdf']) {
    fireEvent.drop(box(), { dataTransfer: { files: [new File(['x'], name)], types: ['Files'] } })
    expect(await screen.findByText(en.attachOnlyImages)).toBeInTheDocument()
  }
  fireEvent.paste(box(), { clipboardData: { files: [new File(['x'], 'b.gif', { type: 'image/gif' })], items: [], getData: () => '' } })
  expect(uploads).toHaveLength(0)
  expect(calls.some((c) => c.method === 'POST')).toBe(false)
})

test('send disabled while a chip is reading', async () => {
  result = [doc({ status: 'processing' })]
  setup()
  await attach(png())
  await screen.findByText(en.attachReading)
  await userEvent.type(box(), 'hi')
  expect(screen.getByRole('button', { name: en.send })).toBeDisabled()
})

test('enter does not send while reading', async () => {
  result = [doc({ status: 'processing' })]
  const { onSend } = setup()
  await attach(png())
  await screen.findByText(en.attachReading)
  await userEvent.type(box(), 'hi{Enter}')
  expect(onSend).not.toHaveBeenCalled()
  expect(box()).toHaveValue('hi')
})

test('failed chip shows the error and is not sent', async () => {
  result = [doc({ status: 'failed', error: 'bad image' })]
  const { onSend } = setup()
  await attach(png())
  expect(await screen.findByText('bad image')).toBeInTheDocument()
  await userEvent.type(box(), 'hi{Enter}')
  expect(onSend).toHaveBeenCalledWith('hi', [])
})

test('not-read chip shows the settings hint and is not sent', async () => {
  result = [doc({ images_unread: 1 })]
  const { onSend } = setup()
  await attach(png())
  expect(await screen.findByText(en.attachUnread)).toBeInTheDocument()
  await userEvent.type(box(), 'hi{Enter}')
  expect(onSend).toHaveBeenCalledWith('hi', [])
})

test('sixth image refused', async () => {
  result = [1, 2, 3, 4, 5].map((i) => doc({ id: `d${i}`, filename: `${i}.png` }))
  setup()
  await attach(...[1, 2, 3, 4, 5].map((i) => png(`${i}.png`)))
  await waitFor(() => expect(screen.getAllByRole('img')).toHaveLength(5))
  await attach(png('6.png'))
  expect(await screen.findByText(en.attachMax)).toBeInTheDocument()
  expect(uploads).toHaveLength(1)
  expect(screen.getAllByRole('img')).toHaveLength(5)
})

test('x removes the chip only (no DELETE request)', async () => {
  setup()
  await attach(png())
  await screen.findByRole('img')
  await userEvent.click(screen.getByRole('button', { name: en.attachRemove }))
  expect(screen.queryByRole('img')).toBeNull()
  expect(revoked).toEqual(['blob:1'])
  expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
})

test('send posts attachments and clears chips', async () => {
  const { onSend } = setup()
  await attach(png())
  await screen.findByRole('img')
  await userEvent.type(box(), 'what is this{Enter}')
  expect(onSend).toHaveBeenCalledTimes(1)
  const [text, atts] = onSend.mock.calls[0]
  expect(text).toBe('what is this')
  expect(atts).toHaveLength(1)
  expect(atts[0]).toMatchObject({ id: 'd1', filename: 'a.png' })
  expect(typeof atts[0].preview).toBe('string')
  expect(screen.queryByRole('img')).toBeNull()
  expect(revoked).toEqual(['blob:1'])
})

test('image alone can be sent without text', async () => {
  const { onSend } = setup()
  await attach(png())
  await screen.findByRole('img')
  await userEvent.click(screen.getByRole('button', { name: en.send }))
  expect(onSend.mock.calls[0][0]).toBe('')
  expect(onSend.mock.calls[0][1]).toHaveLength(1)
})

test('next message has no attachments', async () => {
  const { onSend } = setup()
  await attach(png())
  await screen.findByRole('img')
  await userEvent.type(box(), 'one{Enter}')
  await userEvent.type(box(), 'two{Enter}')
  expect(onSend.mock.calls[1]).toEqual(['two', []])
})

test('paperclip hidden while awaiting a team answer', async () => {
  setup({ awaiting: true })
  expect(picker()).toBeNull()
  expect(screen.queryByRole('button', { name: en.attachImages })).toBeNull()
})
