import { useState } from 'react'
import { useLang } from '@/i18n'
import type { Attachment } from '@/lib/types'
import ImageDialog from './ImageDialog'

function Thumb({ a, onOpen }: { a: Attachment; onOpen(): void }) {
  const { t } = useLang()
  const [broken, setBroken] = useState(false)
  if (a.deleted || broken)
    return (
      <div className="flex size-20 flex-col items-center justify-center gap-1 rounded-lg bg-muted p-1 text-center text-xs text-muted-foreground">
        <span>{t(a.deleted ? 'imageDeleted' : 'noPreview')}</span>
        <span className="max-w-full truncate">{a.filename}</span>
      </div>
    )
  return (
    <button type="button" onClick={onOpen} className="rounded-lg">
      <img src={a.preview} alt={a.filename} onError={() => setBroken(true)} className="size-20 rounded-lg object-cover" />
    </button>
  )
}

export default function UserMessage({ text, attachments = [] }: { text: string; attachments?: Attachment[] }) {
  const [open, setOpen] = useState<Attachment | null>(null)
  return (
    <div className="ml-auto flex w-fit max-w-[820px] flex-col items-end gap-1">
      {attachments.length > 0 && (
        <div className="flex flex-wrap justify-end gap-1">
          {attachments.map((a) => (
            <Thumb key={a.id} a={a} onOpen={() => setOpen(a)} />
          ))}
        </div>
      )}
      {text && <div className="whitespace-pre-wrap rounded-xl bg-muted px-3 py-2">{text}</div>}
      {open && <ImageDialog id={open.id} filename={open.filename} onClose={() => setOpen(null)} />}
    </div>
  )
}
