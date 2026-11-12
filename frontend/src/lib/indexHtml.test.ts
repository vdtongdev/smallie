import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'
import { DEFAULT_THEME } from './themeName'

it('index.html sets the default theme on <html> before the bundle loads', () => {
  const html = readFileSync(resolve(__dirname, '../../index.html'), 'utf8')
  const tag = /<html\b[^>]*>/i.exec(html)?.[0] ?? ''
  expect(/\bdata-theme="([^"]*)"/.exec(tag)?.[1]).toBe(DEFAULT_THEME)
})
