import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { localGet, localSet } from '@/lib/storage'
import { en, type I18nKey } from './en'
import { vi } from './vi'

export type Lang = 'en' | 'vi'

type LangContext = { lang: Lang; setLang(l: Lang): void; t(key: I18nKey): string }

const Ctx = createContext<LangContext | null>(null)
const dictionaries: Record<Lang, Record<I18nKey, string>> = { en, vi }

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => (localGet('lang') === 'vi' ? 'vi' : 'en'))

  useEffect(() => {
    document.documentElement.lang = lang
  }, [lang])

  const setLang = useCallback((l: Lang) => {
    localSet('lang', l)
    setLangState(l)
  }, [])
  const t = useCallback((key: I18nKey) => dictionaries[lang][key] ?? key, [lang])

  return <Ctx.Provider value={{ lang, setLang, t }}>{children}</Ctx.Provider>
}

export function useLang(): LangContext {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useLang must be used inside LanguageProvider')
  return ctx
}
