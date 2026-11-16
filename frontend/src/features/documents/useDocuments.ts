import { createContext, createElement, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
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

const Shared = createContext<Documents | null>(null)

/** One documents state for the folder button, the pop-up and the chat box. */
export function DocumentsProvider({ children }: { children: ReactNode }) {
  return createElement(Shared.Provider, { value: useDocuments() }, children)
}

/** The shared state under a provider; its own instance elsewhere (a component rendered alone). */
export function useSharedDocuments(): Documents {
  const shared = useContext(Shared)
  const own = useDocuments(!!shared)
  return shared ?? own
}

const POLL_MS = 2000

/** The user's files. Polls every 2 s while any file is processing; upload and remove reload the list. */
export function useDocuments(skip = false): Documents {
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
    if (!skip) void refresh()
    return () => {
      alive.current = false
      clearTimeout(timer.current)
    }
  }, [refresh, skip])

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
