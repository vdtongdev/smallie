import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent, type FormEvent, type KeyboardEvent } from 'react'
import { PaperclipIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useSharedDocuments, type Doc } from '@/features/documents/useDocuments'
import { useLang } from '@/i18n'
import type { Attachment } from '@/lib/types'
import AttachmentChips, { type Chip } from './AttachmentChips'

export type Draft = { text: string; id: number }

type Props = { busy: boolean; awaiting: boolean; draft: Draft | null; onSend(text: string, attachments: Attachment[]): void }

const ACCEPT = '.png,.jpg,.jpeg,.webp'
const MAX_IMAGES = 5
const isImage = (f: File) => ACCEPT.split(',').some((ext) => f.name.toLowerCase().endsWith(ext))

type Item = { key: string; filename: string; preview: string; doc?: Doc; error?: string }

export default function Composer({ busy, awaiting, draft, onSend }: Props) {
  const { t } = useLang()
  const { docs, upload } = useSharedDocuments()
  const [value, setValue] = useState('')
  const [items, setItems] = useState<Item[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const box = useRef<HTMLTextAreaElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const itemsRef = useRef<Item[]>([])
  itemsRef.current = items
  const seq = useRef(0)

  useEffect(() => {
    if (draft) {
      setValue(draft.text)
      box.current?.focus()
    }
  }, [draft])

  // The input grows with its text.
  useEffect(() => {
    const el = box.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [value])

  // A team answer carries no images: chips present when the question arrives are dropped.
  useEffect(() => {
    if (!awaiting) return
    itemsRef.current.forEach((i) => URL.revokeObjectURL(i.preview))
    setItems([])
  }, [awaiting])

  // Thumbnails are local object URLs; free them when the box goes away.
  useEffect(() => () => itemsRef.current.forEach((i) => URL.revokeObjectURL(i.preview)), [])

  const chips: (Chip & { id?: string })[] = items.map((i) => {
    const doc = i.doc ? (docs.find((d) => d.id === i.doc!.id) ?? i.doc) : undefined
    const state: Chip['state'] = i.error || doc?.status === 'failed' ? 'failed' : !doc || doc.status === 'processing' ? 'reading' : (doc.images_unread ?? 0) > 0 ? 'unread' : 'ready'
    return { key: i.key, filename: i.filename, preview: i.preview, state, error: i.error ?? doc?.error, id: doc?.id }
  })
  const reading = chips.some((c) => c.state === 'reading')

  const addFiles = (files: File[]) => {
    if (files.length === 0) return
    setNotice(null)
    const images = files.filter(isImage)
    let message = images.length < files.length ? t('attachOnlyImages') : null
    const room = MAX_IMAGES - itemsRef.current.length
    if (images.length > room) message = t('attachMax')
    const take = images.slice(0, Math.max(room, 0))
    if (message) setNotice(message)
    if (take.length === 0) return
    const fresh: Item[] = take.map((f) => ({ key: `a${++seq.current}`, filename: f.name, preview: URL.createObjectURL(f) }))
    setItems((prev) => [...prev, ...fresh])
    const patch = (key: string, p: Partial<Item>) => setItems((prev) => prev.map((i) => (i.key === key ? { ...i, ...p } : i)))
    upload(take).then(
      (docs) => fresh.forEach((i, n) => (docs[n] ? patch(i.key, { doc: docs[n] }) : patch(i.key, { error: t('status_failed') }))),
      (err: Error) => fresh.forEach((i) => patch(i.key, { error: err.message })),
    )
  }

  const clear = () => {
    itemsRef.current.forEach((i) => URL.revokeObjectURL(i.preview))
    setItems([])
  }

  const remove = (key: string) => {
    const gone = itemsRef.current.find((i) => i.key === key)
    if (gone) URL.revokeObjectURL(gone.preview)
    setItems((prev) => prev.filter((i) => i.key !== key))
    setNotice(null)
  }

  const submit = (e?: FormEvent) => {
    e?.preventDefault()
    const text = value.trim()
    const attachments: Attachment[] = awaiting
      ? []
      : chips
      .filter((c) => c.state === 'ready')
      .map((c) => ({ id: c.id!, filename: c.filename, preview: `/api/documents/${c.id}/image?size=thumb` }))
    if ((!text && attachments.length === 0) || busy || reading) return
    setValue('')
    setNotice(null)
    clear()
    onSend(text, attachments)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData?.files ?? [])
    if (files.length === 0) return // plain text pastes as usual
    e.preventDefault()
    addFiles(files)
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    addFiles(Array.from(e.dataTransfer?.files ?? []))
  }

  return (
    <form onSubmit={submit} onDragOver={(e) => e.preventDefault()} onDrop={onDrop} className="flex flex-col gap-2">
      {chips.length > 0 && <AttachmentChips chips={chips} onRemove={remove} />}
      {notice && (
        <p role="alert" className="text-xs text-destructive">
          {notice}
        </p>
      )}
      <div className="flex items-end gap-2">
        {!awaiting && (
          <>
            <input
              type="file"
              accept={ACCEPT}
              multiple
              hidden
              data-testid="chat-file-input"
              onChange={(e) => {
                addFiles(Array.from(e.target.files ?? []))
                e.target.value = ''
              }}
              ref={fileInput}
            />
            <Button
              type="button"
              variant="outline"
              size="icon"
              aria-label={t('attachImages')}
              onClick={() => fileInput.current?.click()}
            >
              <PaperclipIcon />
            </Button>
          </>
        )}
        <Textarea
          ref={box}
          rows={1}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          placeholder={t(awaiting ? 'answerPlaceholder' : 'placeholder')}
          className="max-h-48"
        />
        <Button type="submit" disabled={busy || reading}>
          {t('send')}
        </Button>
      </div>
    </form>
  )
}
