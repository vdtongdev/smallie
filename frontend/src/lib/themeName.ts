import { localGet, localSet } from './storage'

export type ThemeName = 'chatroom' | 'shadcn' | 'smallie'
export const THEMES: readonly ThemeName[] = ['chatroom', 'shadcn', 'smallie']
export const DEFAULT_THEME: ThemeName = 'chatroom'

const isTheme = (v: string | null): v is ThemeName => THEMES.includes(v as ThemeName)

export function resolveThemeName(search: string, stored: string | null): ThemeName {
  const param = new URLSearchParams(search).get('theme')
  if (isTheme(param)) return param
  return isTheme(stored) ? stored : DEFAULT_THEME
}

export function applyThemeName(): ThemeName {
  const param = new URLSearchParams(location.search).get('theme')
  const name = resolveThemeName(location.search, localGet('theme'))
  document.documentElement.dataset.theme = name
  if (isTheme(param)) localSet('theme', name)
  return name
}
