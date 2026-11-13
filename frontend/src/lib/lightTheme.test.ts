import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'

const css = readFileSync(resolve(__dirname, '../index.css'), 'utf8')
const lightBlock = () => /:root\[data-theme="light"\]\s*\{([^}]*)\}/.exec(css)![1]
const token = (block: string, name: string) =>
  new RegExp(`${name}:\\s*([^;]+);`).exec(block)?.[1].trim().toLowerCase()

it('the Light theme main and popup surfaces are white and its header is #faf2dc', () => {
  const block = lightBlock()
  expect(token(block, '--background')).toBe('#ffffff')
  expect(token(block, '--popover')).toBe('#ffffff')
  expect(token(block, '--header')).toBe('#faf2dc')
})

it('every other theme has a header equal to its background', () => {
  expect(css).toContain('--color-header: var(--header);')
  const root = /(?:^|\n):root\s*\{([^}]*)\}/.exec(css)![1]
  expect(token(root, '--header')).toBe('var(--background)')
  const blocks = [...css.matchAll(/:root\[data-theme="(\w+)"\](?:\.dark)?\s*\{([^}]*)\}/g)]
  for (const [, theme, body] of blocks) {
    if (theme !== 'light') expect(token(body, '--header')).toBeUndefined()
  }
})
