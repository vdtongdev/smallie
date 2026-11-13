import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { LanguageProvider } from '@/i18n'
import { en } from '@/i18n/en'
import { buttonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { DocumentsPanel } from './DocumentsPanel'

type Doc = { id: string; filename: string; status: string; chunk_count: number; error: string | null }
let docs: Doc[]
let calls: { method: string; url: string; init?: RequestInit }[]
let gates: Promise<void>[]
let failNext: { status: number; detail: string } | null

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status })

const doc = (over: Partial<Doc> = {}): Doc => ({
  id: 'd1',
  filename: 'a.pdf',
  status: 'ready',
  chunk_count: 7,
  error: null,
  ...over,
})
const listCalls = () => calls.filter((c) => c.method === 'GET' && c.url === '/api/documents').length

beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
  docs = []
  calls = []
  failNext = null
  gates = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, url, init })
      if (failNext && method !== 'GET') {
        const f = failNext
        failNext = null
        return json(f.status, { detail: f.detail })
      }
      if (method === 'GET' && url === '/api/documents') {
        const snapshot = docs
        await gates.shift()
        return json(200, snapshot)
      }
      if (method === 'POST' && url === '/api/documents') return json(200, {})
      if (method === 'DELETE') {
        docs = docs.filter((d) => url.endsWith(`/${d.id}`) === false)
        return json(200, {})
      }
      throw new Error(`unmocked ${method} ${url}`)
    }),
  )
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
  })
}
async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}
async function mount() {
  const r = render(
    <LanguageProvider>
      <DocumentsPanel />
    </LanguageProvider>,
  )
  await flush()
  return r
}

test('lists documents with status badges', async () => {
  docs = [
    doc(),
    doc({ id: 'd2', filename: 'b.docx', status: 'failed', chunk_count: 0, error: 'bad file' }),
  ]
  await mount()
  expect(screen.getByText('a.pdf')).toBeTruthy()
  expect(screen.getByText(`${en.status_ready} · 7 ${en.chunks}`)).toBeTruthy()
  expect(screen.getByText(en.status_failed)).toBeTruthy()
  expect(screen.getByText('b.docx').closest('li')?.getAttribute('title')).toBe('bad file')
})

test('shows the empty text', async () => {
  await mount()
  expect(screen.getByText(en.noDocuments)).toBeTruthy()
})

test('upload posts each file in the files field, then reloads', async () => {
  await mount()
  const input = screen.getByLabelText(en.upload) as HTMLInputElement
  expect(input.accept).toBe('.pdf,.docx')
  expect(input.multiple).toBe(true)
  const f1 = new File(['x'], 'a.pdf')
  const f2 = new File(['y'], 'b.docx')
  docs = [doc()]
  fireEvent.change(input, { target: { files: [f1, f2] } })
  await flush()
  const post = calls.find((c) => c.method === 'POST')!
  const form = post.init!.body as FormData
  expect(form.getAll('files')).toEqual([f1, f2])
  expect(screen.getByText('a.pdf')).toBeTruthy()
})

test('polls every 2 s while processing and stops when ready', async () => {
  docs = [doc({ status: 'processing', chunk_count: 0 })]
  await mount()
  expect(listCalls()).toBe(1)
  await tick(2000)
  expect(listCalls()).toBe(2)
  docs = [doc()]
  await tick(2000)
  expect(listCalls()).toBe(3)
  expect(screen.getByText(`${en.status_ready} · 7 ${en.chunks}`)).toBeTruthy()
  await tick(10000)
  expect(listCalls()).toBe(3)
})

test('unmount stops polling', async () => {
  docs = [doc({ status: 'processing', chunk_count: 0 })]
  const { unmount } = await mount()
  unmount()
  await tick(10000)
  expect(listCalls()).toBe(1)
})

test('delete asks for confirmation, then deletes and reloads', async () => {
  docs = [doc()]
  await mount()
  fireEvent.click(screen.getByRole('button', { name: `${en.delete} a.pdf` }))
  await flush()
  expect(screen.getByText(en.confirmDelete)).toBeTruthy()
  expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: en.delete }))
  await flush()
  expect(calls.find((c) => c.method === 'DELETE')!.url).toBe('/api/documents/d1')
  expect(screen.getByText(en.noDocuments)).toBeTruthy()
})

test('dismissing the confirmation with Escape does not delete', async () => {
  docs = [doc()]
  await mount()
  fireEvent.click(screen.getByRole('button', { name: `${en.delete} a.pdf` }))
  await flush()
  fireEvent.keyDown(screen.getByText(en.confirmDelete), { key: 'Escape' })
  await flush()
  expect(screen.queryByText(en.confirmDelete)).toBeNull()
  expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
})

test('delete is disabled while processing', async () => {
  docs = [doc({ status: 'processing', chunk_count: 0 })]
  await mount()
  const btn = screen.getByRole('button', { name: `${en.delete} a.pdf` }) as HTMLButtonElement
  expect(btn.disabled).toBe(true)
})

test('an upload error shows a destructive alert', async () => {
  await mount()
  failNext = { status: 400, detail: 'Only PDF or DOCX' }
  const input = screen.getByLabelText(en.upload)
  fireEvent.change(input, { target: { files: [new File(['x'], 'a.txt')] } })
  await flush()
  expect(screen.getByRole('alert').textContent).toContain('Only PDF or DOCX')
})

test('Cancel closes the delete confirmation without deleting', async () => {
  docs = [doc()]
  await mount()
  fireEvent.click(screen.getByRole('button', { name: `${en.delete} a.pdf` }))
  await flush()
  fireEvent.click(screen.getByRole('button', { name: en.cancel }))
  await flush()
  expect(screen.queryByText(en.confirmDelete)).toBeNull()
  expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
})

test('a reload during an in-flight poll: the newer list wins and one timer remains', async () => {
  docs = [doc({ status: 'processing', chunk_count: 0 })]
  await mount()
  let releasePoll!: () => void
  let releaseReload!: () => void
  gates = [
    new Promise<void>((r) => (releasePoll = r)),
    new Promise<void>((r) => (releaseReload = r)),
  ]
  await tick(2000) // poll fetch in flight, sees "processing"
  docs = [doc()]
  fireEvent.change(screen.getByLabelText(en.upload), { target: { files: [new File(['x'], 'a.pdf')] } })
  await flush() // upload done, reload in flight, sees "ready"
  releaseReload()
  await tick(100)
  releasePoll() // the older poll answers last
  await tick(100)
  expect(screen.getByText(`${en.status_ready} · 7 ${en.chunks}`)).toBeTruthy()
  const before = listCalls()
  await tick(6000) // nothing is processing: no timer may remain
  expect(listCalls()).toBe(before)
})

test('the upload label gets exactly the classes of an outline Button, so its border shows', async () => {
  await mount()
  const label = screen.getByLabelText(en.upload).closest('label')!
  const expected = [...cn(buttonVariants({ variant: 'outline' })).split(' '), 'w-fit', 'cursor-pointer']
  expect(label.className.split(' ').sort()).toEqual(expected.sort())
  expect(label).not.toHaveClass('border-transparent')
})

test('a document row is a compact bordered card with the delete button next to the status', async () => {
  docs = [doc()]
  await mount()
  const row = screen.getByText('a.pdf').closest('li')!
  expect(row).toHaveClass('border', 'rounded-md', 'w-fit')
  expect(row).not.toHaveClass('w-full')
  expect(screen.getByRole('button', { name: `${en.delete} a.pdf` })).not.toHaveClass('ml-auto')
})
