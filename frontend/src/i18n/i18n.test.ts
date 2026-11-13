import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test } from 'vitest'
import { en } from './en'
import { vi } from './vi'

const old = new Function(
  readFileSync(resolve(__dirname, '../../../app/static/i18n.js'), 'utf8') + '; return I18N;',
)() as { en: Record<string, string>; vi: Record<string, string> }

test('en and vi have the same keys', () => {
  expect(Object.keys(en).sort()).toEqual(Object.keys(vi).sort())
})

test('every key of the old UI exists', () => {
  expect(Object.keys(old.en).length).toBeGreaterThan(100)
  for (const [k, v] of Object.entries(old.en)) expect(en).toHaveProperty([k], v)
  for (const [k, v] of Object.entries(old.vi)) expect(vi).toHaveProperty([k], v)
})
