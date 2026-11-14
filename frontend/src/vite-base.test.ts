import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test } from 'vitest'

const config = readFileSync(resolve(__dirname, '../vite.config.ts'), 'utf8')

test('vite builds for the site root', () => {
  expect(config).toContain("base: '/'")
  expect(config).not.toContain('/next/')
})
