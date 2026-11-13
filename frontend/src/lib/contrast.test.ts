import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(resolve(__dirname, '../index.css'), 'utf8')

type Rgb = [number, number, number]

function parseColor(value: string): Rgb {
  const v = value.trim()
  const hex = /^#([0-9a-f]{6})$/i.exec(v)
  if (hex) {
    const n = parseInt(hex[1], 16)
    return [(n >> 16) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255]
  }
  const rgb = /^rgb\(\s*(\d+)[ ,]+(\d+)[ ,]+(\d+)/i.exec(v)
  if (rgb) return [+rgb[1] / 255, +rgb[2] / 255, +rgb[3] / 255]
  const ok = /^oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)/i.exec(v)
  if (ok) {
    const [l, c, h] = [+ok[1], +ok[2], (+ok[3] * Math.PI) / 180]
    const a = c * Math.cos(h)
    const b = c * Math.sin(h)
    const l_ = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3
    const m_ = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3
    const s_ = (l - 0.0894841775 * a - 1.291485548 * b) ** 3
    const lin = [
      4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
      -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
      -0.0041960863 * l_ - 0.7034186147 * m_ + 1.707614701 * s_,
    ]
    return lin.map((x) => {
      const c2 = Math.min(1, Math.max(0, x))
      return c2 <= 0.0031308 ? c2 * 12.92 : 1.055 * c2 ** (1 / 2.4) - 0.055
    }) as Rgb
  }
  throw new Error(`unsupported colour: ${value}`)
}

function luminance(c: Rgb): number {
  const [r, g, b] = c.map((x) => (x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function ratio(fg: string, bg: string): number {
  const a = luminance(parseColor(fg))
  const b = luminance(parseColor(bg))
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

const blocks = new Map<string, Record<string, string>>()
for (const m of css.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
  const sel = /^\s*:root\[data-theme="(\w+)"\](\.dark)?\s*$/.exec(m[1])
  if (!sel) continue
  const tokens: Record<string, string> = {}
  for (const d of m[2].matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) tokens[d[1]] = d[2].trim()
  blocks.set(`${sel[1]}${sel[2] ? ' dark' : ' light'}`, tokens)
}

const PAIRS: [string, string][] = [
  ['foreground', 'background'],
  ['card-foreground', 'card'],
  ['primary-foreground', 'primary'],
  ['secondary-foreground', 'secondary'],
  ['accent-foreground', 'accent'],
  ['muted-foreground', 'background'],
  ['muted-foreground', 'card'],
  ['destructive', 'background'],
]

// Known failing pairs of the two reference themes (their values are not to change).
const EXCEPTIONS = new Set<string>([])

const TOKENS = [
  'background', 'foreground', 'card', 'card-foreground', 'popover', 'popover-foreground',
  'primary', 'primary-foreground', 'secondary', 'secondary-foreground', 'muted',
  'muted-foreground', 'accent', 'accent-foreground', 'destructive', 'border', 'input', 'ring',
]

describe.each(['chatroom', 'shadcn', 'smallie', 'light'])('theme %s', (theme) => {
  // `light` is the Chatroom light palette with `.dark` forced off, so it has no dark block.
  describe.each(theme === 'light' ? ['light'] : ['light', 'dark'])('%s', (mode) => {
    const tokens = blocks.get(`${theme} ${mode}`)
    it('defines every colour token', () => {
      expect(tokens, `block for ${theme} ${mode}`).toBeDefined()
      for (const t of TOKENS) expect(tokens![t], `--${t}`).toBeTruthy()
    })
    it.each(PAIRS)('%s on %s is at least 4.5:1', (fg, bg) => {
      const key = `${theme} ${mode} ${fg}/${bg}`
      const r = ratio(tokens![fg], tokens![bg])
      if (EXCEPTIONS.has(key)) return
      expect(r, key).toBeGreaterThanOrEqual(4.5)
    })
  })
})
