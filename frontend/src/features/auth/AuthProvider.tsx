import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import type { I18nKey } from '@/i18n/en'
import { api, ApiError, postJson, setUnauthorizedHandler } from '@/lib/api'
import type { Me } from '@/lib/types'
import { signupErrorKey } from './signupError'

type AuthContext = {
  status: 'loading' | 'out' | 'in'
  me: Me | null
  login(username: string, password: string): Promise<boolean>
  signup(name: string, username: string, password: string): Promise<I18nKey | string | null>
  logout(): Promise<void>
}

const Ctx = createContext<AuthContext | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ status: AuthContext['status']; me: Me | null }>({ status: 'loading', me: null })

  const loadMe = useCallback(async () => {
    try {
      setState({ status: 'in', me: await api<Me>('/me') })
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setState({ status: 'out', me: null })
      else throw err
    }
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(() => setState({ status: 'out', me: null }))
    loadMe().catch(() => setState({ status: 'out', me: null }))
  }, [loadMe])

  const login = useCallback(
    async (username: string, password: string) => {
      try {
        const res = await postJson('/login', { username, password })
        if (!res.ok) return false
        await loadMe()
        return true
      } catch {
        return false
      }
    },
    [loadMe],
  )

  const signup = useCallback(
    async (name: string, username: string, password: string) => {
      try {
        const res = await postJson('/signup', { name, username, password })
        if (!res.ok) {
          const detail = ((await res.json().catch(() => ({}))) as { detail?: unknown }).detail
          return signupErrorKey(res.status, detail)
        }
        await loadMe()
        return null
      } catch {
        return 'Error'
      }
    },
    [loadMe],
  )

  const logout = useCallback(async () => {
    await api('/logout', { method: 'POST' }).catch(() => {})
    setState({ status: 'out', me: null })
  }, [])

  const value = useMemo(() => ({ ...state, login, signup, logout }), [state, login, signup, logout])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth(): AuthContext {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
