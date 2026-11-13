import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { initTheme } from './theme'

let listeners: ((e: { matches: boolean }) => void)[] = []

function stubMedia(matches: boolean) {
  listeners = []
  vi.stubGlobal('matchMedia', () => ({
    matches,
    addEventListener: (_: string, fn: (e: { matches: boolean }) => void) => listeners.push(fn),
    removeEventListener: (_: string, fn: (e: { matches: boolean }) => void) => {
      listeners = listeners.filter((l) => l !== fn)
    },
  }))
}

const isDark = () => document.documentElement.classList.contains('dark')

beforeEach(() => document.documentElement.classList.remove('dark'))
afterEach(() => vi.unstubAllGlobals())

test('adds dark when the system is dark', () => {
  stubMedia(true)
  initTheme()
  expect(isDark()).toBe(true)
})

test('removes dark when the system is light', () => {
  document.documentElement.classList.add('dark')
  stubMedia(false)
  initTheme()
  expect(isDark()).toBe(false)
})

test('follows a change while open', () => {
  stubMedia(false)
  const stop = initTheme()
  listeners.forEach((l) => l({ matches: true }))
  expect(isDark()).toBe(true)
  listeners.forEach((l) => l({ matches: false }))
  expect(isDark()).toBe(false)
  stop()
  expect(listeners).toHaveLength(0)
})

describe('theme decides dark', () => {
  afterEach(() => delete document.documentElement.dataset.theme)

  test('chatroom is dark even when the system is light', () => {
    document.documentElement.dataset.theme = 'chatroom'
    stubMedia(false)
    initTheme()
    expect(isDark()).toBe(true)
  })

  test('light is not dark even when the system is dark', () => {
    document.documentElement.dataset.theme = 'light'
    document.documentElement.classList.add('dark')
    stubMedia(true)
    initTheme()
    expect(isDark()).toBe(false)
  })

  test('chatroom and light ignore a system change', () => {
    document.documentElement.dataset.theme = 'chatroom'
    stubMedia(false)
    initTheme()
    listeners.forEach((l) => l({ matches: false }))
    expect(isDark()).toBe(true)
    document.documentElement.dataset.theme = 'light'
    listeners.forEach((l) => l({ matches: true }))
    expect(isDark()).toBe(false)
  })

  test('shadcn and smallie follow the system', () => {
    for (const name of ['shadcn', 'smallie']) {
      document.documentElement.dataset.theme = name
      stubMedia(true)
      const stop = initTheme()
      expect(isDark()).toBe(true)
      listeners.forEach((l) => l({ matches: false }))
      expect(isDark()).toBe(false)
      stop()
    }
  })
})
