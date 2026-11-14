import { localGet, localSet } from './storage'
import { applyDark, systemDark } from './theme'

export type ThemeName = 'chatroom' | 'shadcn' | 'light'
export const THEMES: readonly ThemeName[] = ['chatroom', 'shadcn', 'light']
/** What the Settings picker offers. */
export const PICKER_THEMES: readonly ThemeName[] = ['shadcn', 'chatroom', 'light']
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

/** The picker's choice: applies at once and is remembered for this browser. */
export function setThemeName(name: ThemeName): void {
  document.documentElement.dataset.theme = name
  localSet('theme', name)
  applyDark(systemDark())
}
