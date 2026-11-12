import { useCallback, useEffect, useRef, useState } from 'react'
import { useAuth } from '@/features/auth/AuthProvider'
import { useLang } from '@/i18n'
import { api, ApiError, postJson, unauthorized } from '@/lib/api'
import { turnsFromHistory, type HistoryMessage } from '@/lib/history'
import { readEvents } from '@/lib/stream'
import { endTurn, newTurn, reduceTurn, type Turn } from '@/lib/turn'
import type { Conversation, Usage } from '@/lib/types'
import { localGet, localSet } from '@/lib/storage'

export type Chat = ReturnType<typeof useChat>

export function useChat() {
  const { me } = useAuth()
  const { t } = useLang()
  const uid = me?.user_id
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [busy, setBusy] = useState(false)
  const [usage, setUsage] = useState<Usage | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [ready, setReady] = useState(false)

  const currentRef = useRef<string | null>(null)
  const busyRef = useRef(false)
  const epoch = useRef(0) // bumped when the open chat changes, so a reply for the old one stops drawing
  const kinds = useRef(new Map<string, string>())

  const remember = useCallback(
    (id: string | null) => {
      if (uid) localSet(`conversation_auto_${uid}`, id ?? '')
    },
    [uid],
  )

  const loadConversations = useCallback(async () => {
    const items = await api<Conversation[]>('/conversations').catch(() => [] as Conversation[])
    kinds.current = new Map(items.map((c) => [c.id, c.kind]))
    setConversations(items)
  }, [])

  const reset = useCallback(() => {
    epoch.current++
    currentRef.current = null
    setCurrentId(null)
    setTurns([])
    setUsage(null)
    setLoadError(null)
    remember(null)
  }, [remember])

  const newChat = useCallback(() => {
    if (!busyRef.current) reset()
  }, [reset])

  const open = useCallback(
    async (id: string) => {
      if (busyRef.current) return
      epoch.current++
      currentRef.current = id
      setCurrentId(id)
      remember(id)
      setTurns([])
      setUsage(null)
      setLoadError(null)
      let messages: HistoryMessage[]
      try {
        messages = await api<HistoryMessage[]>(`/conversations/${id}/messages`)
      } catch (err) {
        if (currentRef.current !== id) return
        if (err instanceof ApiError && err.message === 'Conversation not found') return reset()
        setLoadError(`${t('loadFailed')}: ${(err as Error).message}`)
        return
      }
      if (currentRef.current !== id) return // the user switched away while this loaded
      setTurns(turnsFromHistory(messages))
      const loaded = await api<Usage>(`/conversations/${id}/usage`).catch(() => null)
      if (currentRef.current === id) setUsage(loaded)
    },
    [remember, reset, t],
  )

  useEffect(() => {
    if (!uid) return
    let cancelled = false
    void (async () => {
      await loadConversations()
      const stored = localGet(`conversation_auto_${uid}`)
      if (stored) await open(stored)
      else reset()
      if (!cancelled) setReady(true)
    })()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uid])

  const send = useCallback(
    async (text: string) => {
      const message = text.trim()
      if (busyRef.current || !message) return
      busyRef.current = true
      setBusy(true)
      const run = epoch.current
      const kind = kinds.current.get(currentRef.current ?? '') === 'team' ? 'team' : 'auto'
      let turn = newTurn(message)
      setTurns((prev) => [...prev, turn])
      const update = (next: Turn) => {
        turn = next
        if (epoch.current === run) setTurns((prev) => [...prev.slice(0, -1), next])
      }
      try {
        const res = await postJson(`/chat/${kind}`, { message, conversation_id: currentRef.current })
        if (res.status === 401) {
          unauthorized()
          return
        }
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as { detail?: unknown }
          throw new Error(typeof body.detail === 'string' ? body.detail : res.statusText)
        }
        if (!res.body) throw new Error(res.statusText)
        for await (const event of readEvents(res.body)) {
          if (event.type === 'conversation') {
            const id = event.id as string
            kinds.current.set(id, kind)
            if (epoch.current === run) {
              currentRef.current = id
              setCurrentId(id)
              remember(id)
            }
          } else if (event.type === 'usage') {
            if (epoch.current === run) setUsage(event as unknown as Usage)
          } else update(reduceTurn(turn, event))
        }
        update(endTurn(turn))
      } catch (err) {
        update(endTurn(turn, (err as Error).message))
      } finally {
        busyRef.current = false
        setBusy(false)
        void loadConversations()
      }
    },
    [loadConversations, remember],
  )

  const remove = useCallback(
    async (id: string) => {
      await api(`/conversations/${id}`, { method: 'DELETE' })
      if (id === currentRef.current) reset()
      await loadConversations()
    },
    [loadConversations, reset],
  )

  return { conversations, currentId, turns, busy, usage, loadError, ready, send, open, newChat, remove }
}
