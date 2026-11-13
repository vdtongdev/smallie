import { afterEach, describe, expect, it, vi } from 'vitest'
import { applyThemeName, DEFAULT_THEME, resolveThemeName, THEMES } from './themeName'

describe('resolveThemeName', () => {
  it('lists the three themes with chatroom as default', () => {
    expect([...THEMES]).toEqual(['chatroom', 'shadcn', 'smallie'])
    expect(DEFAULT_THEME).toBe('chatroom')
  })
  it('prefers the query param over the stored value', () => {
    expect(resolveThemeName('?theme=shadcn', 'smallie')).toBe('shadcn')
  })
  it('uses the stored value without a param', () => {
    expect(resolveThemeName('', 'smallie')).toBe('smallie')
  })
  it('falls back to the stored value when the param is invalid', () => {
    expect(resolveThemeName('?theme=nope', 'smallie')).toBe('smallie')
  })
  it('defaults to chatroom with nothing', () => {
    expect(resolveThemeName('', null)).toBe('chatroom')
  })
  it('defaults to chatroom when the stored value is invalid', () => {
    expect(resolveThemeName('', 'nope')).toBe('chatroom')
  })
})

describe('applyThemeName', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
    delete document.documentElement.dataset.theme
    window.history.replaceState(null, '', '/')
  })

  it('sets data-theme and stores a valid param', () => {
    window.history.replaceState(null, '', '/?theme=shadcn')
    expect(applyThemeName()).toBe('shadcn')
    expect(document.documentElement.dataset.theme).toBe('shadcn')
    expect(localStorage.getItem('theme')).toBe('shadcn')
  })
  it('uses the stored theme and does not rewrite it when there is no param', () => {
    localStorage.setItem('theme', 'smallie')
    expect(applyThemeName()).toBe('smallie')
    expect(document.documentElement.dataset.theme).toBe('smallie')
  })
  it('still applies the theme when storage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    window.history.replaceState(null, '', '/?theme=smallie')
    expect(applyThemeName()).toBe('smallie')
    expect(document.documentElement.dataset.theme).toBe('smallie')
  })
})
