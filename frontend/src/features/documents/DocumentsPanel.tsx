import { useCallback, useEffect, useRef, useState, type ChangeEvent } from 'react'
import { XIcon } from 'lucide-react'
import { useLang } from '@/i18n'
import { api } from '@/lib/api'
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
import { buttonVariants } from '@/components/ui/button'

type Doc = {
  id: string
  filename: string
  status: 'processing' | 'ready' | 'failed'
  chunk_count: number
  error: string | null
}

const POLL_MS = 2000

export function DocumentsPanel() {
  const { t } = useLang()
  const [docs, setDocs] = useState<Doc[]>([])
  const [error, setError] = useState('')
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const alive = useRef(true)
  const seq = useRef(0)

  const load = useCallback(async () => {
    clearTimeout(timer.current)
    const mine = ++seq.current
    const list = await api<Doc[]>('/documents').catch(() => [] as Doc[])
    if (!alive.current || mine !== seq.current) return
    setDocs(list)
    if (list.some((d) => d.status === 'processing')) timer.current = setTimeout(load, POLL_MS)
  }, [])

  useEffect(() => {
    alive.current = true
    void load()
    return () => {
      alive.current = false
      clearTimeout(timer.current)
    }
  }, [load])

  async function upload(e: ChangeEvent<HTMLInputElement>) {
    const input = e.target
    const files = Array.from(input.files ?? [])
    if (!files.length) return
    const form = new FormData()
    files.forEach((f) => form.append('files', f))
    setError('')
    await api('/documents', { method: 'POST', body: form }).catch((err: Error) => setError(err.message))
    input.value = ''
    await load()
  }

  async function remove(id: string) {
    setError('')
    await api(`/documents/${id}`, { method: 'DELETE' }).catch((err: Error) => setError(err.message))
    await load()
  }

  return (
    <div className="flex flex-col gap-2 text-sm">
      <label className={`${buttonVariants({ variant: 'outline' })} w-fit cursor-pointer`}>
        {t('upload')}
        <input type="file" accept=".pdf,.docx" multiple className="sr-only" onChange={upload} />
      </label>
      {error && <Alert variant="destructive">{error}</Alert>}
      <ul className="flex flex-col gap-1">
        {docs.length === 0 && <li className="text-muted-foreground">{t('noDocuments')}</li>}
        {docs.map((d) => (
          <li key={d.id} title={d.error ?? ''} className="flex items-center gap-2">
            <span className="truncate">{d.filename}</span>
            <span className="text-muted-foreground">
              {t(`status_${d.status}`) + (d.status === 'ready' ? ` · ${d.chunk_count} ${t('chunks')}` : '')}
            </span>
            <AlertDialog>
              <AlertDialogTrigger
                disabled={d.status === 'processing'}
                aria-label={`${t('delete')} ${d.filename}`}
                className="ml-auto disabled:opacity-50"
              >
                <XIcon className="size-4" />
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogTitle>{t('confirmDelete')}</AlertDialogTitle>
                <AlertDialogFooter>
                  <AlertDialogCancel>{t('cancel')}</AlertDialogCancel>
                  <AlertDialogAction onClick={() => void remove(d.id)}>{t('delete')}</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </li>
        ))}
      </ul>
    </div>
  )
}
