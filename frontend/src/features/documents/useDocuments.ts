import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'

export type Doc = {
  id: string
  filename: string
  status: 'processing' | 'ready' | 'failed'
  chunk_count: number
  image_count?: number
  images_unread?: number
  has_image?: boolean
  error: string | null
}

export type Documents = {
  docs: Doc[]
  upload(files: File[]): Promise<Doc[]>
  remove(id: string): Promise<void>
  refresh(): Promise<void>
}

const POLL_MS = 2000

/** The user's files. Polls every 2 s while any file is processing; upload and remove reload the list. */
export function useDocuments(): Documents {
  const [docs, setDocs] = useState<Doc[]>([])
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const alive = useRef(true)
  const seq = useRef(0)

  const refresh = useCallback(async () => {
    clearTimeout(timer.current)
    const mine = ++seq.current
    const list = await api<Doc[]>('/documents').catch(() => [] as Doc[])
    if (!alive.current || mine !== seq.current) return
    setDocs(list)
    if (list.some((d) => d.status === 'processing')) timer.current = setTimeout(refresh, POLL_MS)
  }, [])

  useEffect(() => {
    alive.current = true
    void refresh()
    return () => {
      alive.current = false
      clearTimeout(timer.current)
    }
  }, [refresh])

  const upload = useCallback(
    async (files: File[]) => {
      const form = new FormData()
      files.forEach((f) => form.append('files', f))
      try {
        return await api<Doc[]>('/documents', { method: 'POST', body: form })
      } finally {
        await refresh()
      }
    },
    [refresh],
  )

  const remove = useCallback(
    async (id: string) => {
      try {
        await api(`/documents/${id}`, { method: 'DELETE' })
      } finally {
        await refresh()
      }
    },
    [refresh],
  )

  return { docs, upload, remove, refresh }
}
