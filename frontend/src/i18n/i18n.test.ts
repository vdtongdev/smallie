import { expect, test } from 'vitest'
import { en } from './en'
import { vi } from './vi'

test('en and vi have the same keys', () => {
  expect(Object.keys(en).sort()).toEqual(Object.keys(vi).sort())
})
