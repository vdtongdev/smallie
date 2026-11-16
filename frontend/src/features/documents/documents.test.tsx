import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { LanguageProvider } from '@/i18n'
import { en } from '@/i18n/en'
import { DocumentsDialog } from './DocumentsDialog'
import { useDocuments } from './useDocuments'

function Harness() {
  const documents = useDocuments()
  return <DocumentsDialog open onOpenChange={() => {}} documents={documents} />
}

type Doc = {
  id: string
  filename: string
  status: string
  chunk_count: number
  error: string | null
  image_count?: number
  images_unread?: number
  has_image?: boolean
}
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
const fileInput = () => screen.getByTestId('file-input') as HTMLInputElement
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
      if (method === 'POST' && url === '/api/documents') return json(200, [{ id: 'n1', filename: 'a.pdf', status: 'processing' }])
      if (method === 'DELETE') {
        docs = docs.filter((d) => url.endsWith(`/${d.id}`) === false)
        return json(204)
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
      <Harness />
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

test('shows image count on a ready file', async () => {
  docs = [doc({ image_count: 3, images_unread: 0 })]
  await mount()
  expect(screen.getByText(/· 3 images/)).toBeTruthy()
})

test('shows the unread note', async () => {
  docs = [doc({ image_count: 0, images_unread: 2 })]
  await mount()
  expect(screen.getByText(/· 2 images not read: set an image model in Settings/)).toBeTruthy()
})

test('file input accepts images', async () => {
  await mount()
  const input = fileInput()
  expect(input.accept).toContain('.png,.jpg,.jpeg,.webp')
})

test('shows the empty text', async () => {
  await mount()
  expect(screen.getByText(en.noDocuments)).toBeTruthy()
})

test('upload posts each file in the files field, then reloads', async () => {
  await mount()
  const input = fileInput()
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
  const input = fileInput()
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
  fireEvent.change(fileInput(), { target: { files: [new File(['x'], 'a.pdf')] } })
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

test('left list shows status, chunks, images and the unread note, no size', async () => {
  docs = [doc({ image_count: 3, images_unread: 2, size: 12345 } as Partial<Doc>)]
  await mount()
  const row = screen.getByText('a.pdf').closest('li')!
  expect(row.textContent).toContain(`${en.status_ready} · 7 ${en.chunks} · 3 ${en.images} · 2 ${en.imagesUnread}`)
  expect(row.textContent).not.toMatch(/KB|MB|12345/)
})

test('image files with has_image show a thumbnail, others do not', async () => {
  docs = [doc({ id: 'i1', filename: 'p.png', has_image: true }), doc({ id: 'd2', filename: 'b.pdf' })]
  const { container } = await mount()
  const imgs = container.ownerDocument.querySelectorAll('img')
  expect(imgs).toHaveLength(1)
  expect(imgs[0].getAttribute('src')).toBe('/api/documents/i1/image?size=thumb')
  expect(screen.getByText('b.pdf').closest('li')!.querySelector('img')).toBeNull()
})

test('drop zone uploads dropped files', async () => {
  await mount()
  const f = new File(['x'], 'a.pdf')
  fireEvent.drop(screen.getByText(en.dropFiles).closest('[data-testid="drop-zone"]')!, { dataTransfer: { files: [f] } })
  await flush()
  expect((calls.find((c) => c.method === 'POST')!.init!.body as FormData).getAll('files')).toEqual([f])
})

test('Upload files button opens the picker and uploads', async () => {
  await mount()
  const click = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {})
  fireEvent.click(screen.getByRole('button', { name: en.uploadFiles }))
  expect(click).toHaveBeenCalled()
  click.mockRestore()
  const f = new File(['x'], 'a.pdf')
  fireEvent.change(fileInput(), { target: { files: [f] } })
  await flush()
  expect((calls.find((c) => c.method === 'POST')!.init!.body as FormData).getAll('files')).toEqual([f])
  expect(screen.getByText(en.acceptedTypes)).toBeTruthy()
})

test('narrow screen stacks the upload area above the list', async () => {
  await mount()
  const zone = screen.getByTestId('drop-zone')
  expect(zone).toHaveClass('order-first', 'md:order-last')
  expect(zone.parentElement).toHaveClass('flex-col', 'md:flex-row')
})

test('dialog is 70% wide and 60% tall', async () => {
  await mount()
  const dialog = screen.getByRole('dialog')
  expect(dialog).toHaveClass('md:w-[70vw]', 'h-[60vh]', 'md:max-w-none')
  expect(dialog.className).not.toMatch(/max-w-(lg|sm|\[)/)
})

test('the two panes have equal width', async () => {
  await mount()
  const zone = screen.getByTestId('drop-zone')
  const list = screen.getByRole('list')
  for (const pane of [zone, list]) expect(pane).toHaveClass('md:flex-1', 'md:basis-0', 'min-w-0')
  expect(zone.className).not.toMatch(/md:w-64/)
})

test('a long file name wraps instead of overflowing', async () => {
  docs = [doc({ filename: 'a-very-long-file-name-without-any-break-points-at-all.pdf' })]
  await mount()
  const name = screen.getByText('a-very-long-file-name-without-any-break-points-at-all.pdf')
  expect(name).toHaveClass('break-words')
  expect(name.className).not.toMatch(/truncate|whitespace-nowrap/)
})

test('clicking an image name in the pop-up opens the full image with its name', async () => {
  docs = [doc({ id: 'i1', filename: 'p.png', has_image: true })]
  await mount()
  fireEvent.click(screen.getByText('p.png'))
  const full = screen.getByAltText('p.png')
  expect(full.getAttribute('src')).toBe('/api/documents/i1/image?size=full')
  expect(screen.getByRole('heading', { name: 'p.png' })).toBeTruthy()
})

test('documents without a stored image have no viewer', async () => {
  docs = [doc({ id: 'd2', filename: 'b.pdf' }), doc({ id: 'i2', filename: 'q.png', has_image: false })]
  await mount()
  expect(screen.queryByRole('button', { name: /^View / })).toBeNull()
})

test('closing the viewer keeps the Documents pop-up open', async () => {
  docs = [doc({ id: 'i1', filename: 'p.png', has_image: true })]
  await mount()
  fireEvent.click(screen.getByText('p.png'))
  expect(screen.getByAltText('p.png')).toBeTruthy()
  fireEvent.keyDown(screen.getByAltText('p.png'), { key: 'Escape' })
  await flush()
  expect(screen.queryByAltText('p.png')).toBeNull()
  expect(screen.getByRole('heading', { name: en.documents })).toBeTruthy()
})

test('the thumbnail is not clickable', async () => {
  docs = [doc({ id: 'i1', filename: 'p.png', has_image: true })]
  const { container } = await mount()
  const img = container.ownerDocument.querySelector('img')!
  expect(img.closest('button')).toBeNull()
  fireEvent.click(img)
  expect(screen.queryByAltText('p.png')).toBeNull()
})

test('the image name is a link-styled button', async () => {
  docs = [doc({ id: 'i1', filename: 'p.png', has_image: true })]
  await mount()
  const name = screen.getByRole('button', { name: 'View p.png' })
  expect(name).toHaveTextContent('p.png')
  expect(name).toHaveClass('cursor-pointer', 'hover:underline', 'text-primary', 'break-words', 'min-w-0')
})

test('a non-image file name is plain text', async () => {
  docs = [doc({ id: 'd2', filename: 'b.pdf' })]
  await mount()
  expect(screen.getByText('b.pdf').closest('button')).toBeNull()
})
