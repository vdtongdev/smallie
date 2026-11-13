import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'

const css = readFileSync(resolve(__dirname, '../index.css'), 'utf8')

it('the Light theme main surface is #e5d0a5', () => {
  const block = /:root\[data-theme="light"\]\s*\{([^}]*)\}/.exec(css)
  expect(block).not.toBeNull()
  expect(/--background:\s*([^;]+);/.exec(block![1])![1].trim().toLowerCase()).toBe('#e5d0a5')
})
