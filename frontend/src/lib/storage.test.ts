import { afterEach, expect, test, vi } from 'vitest'
import { localGet, localSet } from './storage'

afterEach(() => vi.restoreAllMocks())

test('localGet returns null when storage throws', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('blocked')
  })
  expect(localGet('lang')).toBeNull()
})

test('localSet does not throw when storage throws', () => {
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('blocked')
  })
  expect(() => localSet('lang', 'vi')).not.toThrow()
})
