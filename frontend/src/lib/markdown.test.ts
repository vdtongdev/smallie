import { describe, expect, it } from 'vitest'
import { linkCitations, plainMath, renderMarkdown } from './markdown'

function render(md: string): HTMLElement {
  const el = document.createElement('div')
  el.innerHTML = renderMarkdown(md)
  return el
}

describe('plainMath', () => {
  it('turns $\\rightarrow$ into an arrow', () => {
    expect(plainMath('a $\\rightarrow$ b')).toBe('a → b')
  })
  it('leaves "$5 to $10" unchanged', () => {
    expect(plainMath('costs $5 to $10')).toBe('costs $5 to $10')
  })
  it('converts \\times outside $', () => {
    expect(plainMath('3 \\times 4')).toBe('3 × 4')
  })
})

describe('renderMarkdown', () => {
  it('removes script tags and onerror attributes', () => {
    const html = renderMarkdown('hi <script>alert(1)</script> <img src=x onerror=alert(1)>')
    expect(html).not.toContain('<script')
    expect(html).not.toContain('onerror')
  })
  it('opens http links in a new tab safely', () => {
    const a = render('[a](https://x.com)').querySelector('a')!
    expect(a.getAttribute('target')).toBe('_blank')
    expect(a.getAttribute('rel')).toBe('noopener noreferrer')
  })
  it('makes a bare domain link absolute', () => {
    const a = render('[a](example.com/page)').querySelector('a')!
    expect(a.getAttribute('href')).toBe('https://example.com/page')
  })
})

describe('linkCitations', () => {
  const list = '\n\n1. First source\n2. Second source\n'

  it('links [1][2] to the final list items', () => {
    const el = render('A.[1][2]' + list)
    linkCitations(el)
    const links = [...el.querySelectorAll('a.cite')] as HTMLAnchorElement[]
    expect(links.map((a) => a.dataset.ref)).toEqual(['1', '2'])
    expect(links.map((a) => a.title)).toEqual(['First source', 'Second source'])
  })
  it('keeps an out-of-range number as text', () => {
    const el = render('A.[3]' + list)
    linkCitations(el)
    expect(el.querySelector('a.cite')).toBeNull()
    expect(el.textContent).toContain('[3]')
  })
  it('keeps [1] inside code as text', () => {
    const el = render('Use `x[1]` here' + list)
    linkCitations(el)
    expect(el.querySelector('a.cite')).toBeNull()
    expect(el.querySelector('code')!.textContent).toBe('x[1]')
  })
  it('links [1, 2] as two links', () => {
    const el = render('A.[1, 2]' + list)
    linkCitations(el)
    expect(el.querySelectorAll('a.cite')).toHaveLength(2)
  })
})
