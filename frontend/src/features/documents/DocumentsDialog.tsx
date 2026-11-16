import { useRef, useState, type ChangeEvent, type DragEvent } from 'react'
import { XIcon } from 'lucide-react'
import { useLang } from '@/i18n'
import { Alert } from '@/components/ui/alert'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import type { Documents } from './useDocuments'

export function DocumentsDialog({
  open,
  onOpenChange,
  documents,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  documents: Documents
}) {
  const { t } = useLang()
  const { docs, upload, remove } = documents
  const [error, setError] = useState('')
  const [dragging, setDragging] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  async function send(files: File[]) {
    if (!files.length) return
    setError('')
    await upload(files).catch((err: Error) => setError(err.message))
  }
  async function onPick(e: ChangeEvent<HTMLInputElement>) {
    const el = e.target
    await send(Array.from(el.files ?? []))
    el.value = ''
  }
  function onDrop(e: DragEvent) {
    e.preventDefault()
    setDragging(false)
    void send(Array.from(e.dataTransfer.files))
  }
  async function del(id: string) {
    setError('')
    await remove(id).catch((err: Error) => setError(err.message))
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>{t('documents')}</DialogTitle>
        </DialogHeader>
        {error && <Alert variant="destructive">{error}</Alert>}
        <div className="flex flex-col gap-4 md:flex-row">
          <ul className="flex min-w-0 flex-1 flex-col gap-1 overflow-y-auto md:max-h-96">
            {docs.length === 0 && <li className="text-muted-foreground">{t('noDocuments')}</li>}
            {docs.map((d) => (
              <li key={d.id} title={d.error ?? ''} className="flex items-center gap-2 rounded-md border border-border px-2 py-1">
                {d.has_image && (
                  <img
                    src={`/api/documents/${d.id}/image?size=thumb`}
                    alt=""
                    className="size-8 shrink-0 rounded object-cover"
                  />
                )}
                <span className="min-w-0 truncate">{d.filename}</span>
                <span className="text-muted-foreground">
                  {t(`status_${d.status}`) +
                    (d.status === 'ready'
                      ? ` · ${d.chunk_count} ${t('chunks')}` +
                        (d.image_count ? ` · ${d.image_count} ${t('images')}` : '') +
                        (d.images_unread ? ` · ${d.images_unread} ${t('imagesUnread')}` : '')
                      : '')}
                </span>
                <AlertDialog>
                  <AlertDialogTrigger
                    disabled={d.status === 'processing'}
                    aria-label={`${t('delete')} ${d.filename}`}
                    className="disabled:opacity-50"
                  >
                    <XIcon className="size-4" />
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogTitle>{t('confirmDelete')}</AlertDialogTitle>
                    <AlertDialogFooter>
                      <AlertDialogCancel>{t('cancel')}</AlertDialogCancel>
                      <AlertDialogAction onClick={() => void del(d.id)}>{t('delete')}</AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </li>
            ))}
          </ul>
          <div
            data-testid="drop-zone"
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            className={cn(
              'order-first flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-border p-6 text-center md:order-last md:w-64 md:shrink-0',
              dragging && 'border-primary',
            )}
          >
            <span>{t('dropFiles')}</span>
            <Button variant="outline" onClick={() => input.current?.click()}>
              {t('uploadFiles')}
            </Button>
            <span className="text-xs text-muted-foreground">{t('acceptedTypes')}</span>
            <input
              ref={input}
              data-testid="file-input"
              type="file"
              accept=".pdf,.docx,.png,.jpg,.jpeg,.webp"
              multiple
              className="sr-only"
              tabIndex={-1}
              onChange={onPick}
            />
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
