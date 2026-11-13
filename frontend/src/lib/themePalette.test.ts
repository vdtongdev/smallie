import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { expect, it } from 'vitest'

const palette = resolve(__dirname, '../../theme-palette')
const src = resolve(__dirname, '..')

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n)
    return statSync(p).isDirectory() ? files(p) : [p]
  })
}

it('has the Chatroom reference image and a README', () => {
  expect(existsSync(join(palette, 'chatroom.png'))).toBe(true)
  const readme = readFileSync(join(palette, 'README.md'), 'utf8')
  for (const hex of ['#b1ab9d', '#e5d0a5', '#5f706a', '#b0785d', '#60797d', '#f4e5bc', '#8a7363', '#303d4d', '#cdb593']) {
    expect(readme.toLowerCase()).toContain(hex)
  }
})

it('is not referenced from src, so it never ships', () => {
  const name = 'theme' + '-palette'
  for (const f of files(src)) {
    if (!/\.(tsx?|css|html)$/.test(f) || f === __filename) continue
    expect(readFileSync(f, 'utf8'), f).not.toContain(name)
  }
})
