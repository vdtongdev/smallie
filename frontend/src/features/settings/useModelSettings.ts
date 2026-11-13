import { useCallback, useEffect, useRef, useState } from 'react'
import { useLang } from '@/i18n'
import { api, ApiError } from '@/lib/api'
import { cachedModels, PROFILES, storeModels, type Slots } from '@/lib/modelSettings'

type Saved = Partial<Slots> & { key_hint?: string }
export type SaveResult = { warning?: string; warnings?: Record<string, string> }

const EMPTY: Slots = { base_url: '', model: '', small_model: '', strong_model: '', profile: 'balanced' }

/** The saved model settings, the provider's cached model list and the calls that change them (app.js 1112-1245). */
export function useModelSettings(open: boolean) {
  const { t } = useLang()
  const [loaded, setLoaded] = useState<Slots>(EMPTY)
  const [hint, setHint] = useState('')
  const [status, setStatus] = useState('')
  const [models, setModels] = useState<string[]>([])
  const loadedRef = useRef(loaded)
  loadedRef.current = loaded
  const started = useRef(false)

  /** Resolves to the saved slots, `null` when nothing is saved, `undefined` when the load failed. */
  const load = useCallback(async () => {
    let s: Saved | null = null
    try {
      s = await api<Saved>('/model-settings')
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) s = null // nothing saved yet
      else {
        setStatus(err instanceof Error ? err.message : String(err))
        return undefined
      }
    }
    const profile = PROFILES.find((p) => p === s?.profile) ?? 'balanced'
    const slots: Slots = {
      base_url: s?.base_url ?? '',
      model: s?.model ?? '',
      small_model: s?.small_model ?? '',
      strong_model: s?.strong_model ?? '',
      profile,
    }
    loadedRef.current = slots
    setLoaded(slots)
    setHint(s?.key_hint ?? '')
    setModels(cachedModels(slots.base_url))
    setStatus(s ? t('settingsKeySaved') : t('settingsNone'))
    return s ? slots : null
  }, [t])

  /** Fetches the provider's list for the saved endpoint and keeps it. */
  const refresh = useCallback(async (baseUrl: string) => {
    const res = await api<{ models: string[] }>('/model-settings/models').catch(() => null)
    if (!baseUrl || !res?.models?.length) return
    storeModels(baseUrl, res.models)
    if (baseUrl === loadedRef.current.base_url) setModels(cachedModels(baseUrl))
  }, [])

  // Once after login in the background, then again each time the dialog opens.
  useEffect(() => {
    const first = !started.current
    if (!open && !first) return
    started.current = true
    void load().then((s) => {
      if (first && s) void refresh(s.base_url)
    })
  }, [open, load, refresh])

  /** PUTs the form; an empty `key` keeps the saved key (`api_key: null`). Throws when the server refuses. */
  const save = useCallback(
    async (next: Slots, key: string): Promise<SaveResult> => {
      const res = await api<SaveResult>('/model-settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...next, api_key: key || null }),
      })
      const endpointChanged = next.base_url !== loadedRef.current.base_url
      await load()
      if (endpointChanged || !cachedModels(next.base_url).length) await refresh(next.base_url)
      return res
    },
    [load, refresh],
  )

  const clear = useCallback(async () => {
    try {
      await api('/model-settings', { method: 'DELETE' })
      await load()
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err))
    }
  }, [load])

  return { loaded, hint, status, setStatus, models, save, clear }
}
