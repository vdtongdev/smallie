import DOMPurify from 'dompurify'
import { marked } from 'marked'

// A link written as "example.com/page" (no scheme) would resolve inside the app; make it absolute.
const BARE_DOMAIN =
  /^(?!.*\.(?:pdf|docx?)$)(?:www\.)?[\p{L}\d-]+(?:\.[\p{L}\d-]+)*\.[a-z]{2,}(?:[/?#]|$)/iu

// Source links open in a new tab, so clicking one doesn't leave the chat.
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName !== 'A') return
  const href = node.getAttribute('href') || ''
  if (BARE_DOMAIN.test(href)) node.setAttribute('href', `https://${href}`)
  if (/^https?:/i.test(node.getAttribute('href') ?? '')) {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

export function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(marked.parse(plainMath(text || '')) as string)
}

// Models sometimes write LaTeX ("$\rightarrow$", "$5\%$"), which marked shows raw. Known commands
// become Unicode; a $...$ span loses its $ only when nothing TeX is left, so "$5 to $10" is untouched.
const TEX_SYMBOLS: Record<string, string> = {
  rightarrow: '→', to: '→', longrightarrow: '⟶', leftarrow: '←', gets: '←', leftrightarrow: '↔',
  Rightarrow: '⇒', implies: '⇒', Leftarrow: '⇐', Leftrightarrow: '⇔', iff: '⇔',
  uparrow: '↑', downarrow: '↓', nearrow: '↗', searrow: '↘',
  times: '×', div: '÷', pm: '±', cdot: '·', approx: '≈', sim: '~', neq: '≠', ne: '≠',
  le: '≤', leq: '≤', ge: '≥', geq: '≥', ll: '≪', gg: '≫', infty: '∞', circ: '°', degree: '°',
  ldots: '…', dots: '…', cdots: '⋯', checkmark: '✓',
  alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', Delta: 'Δ', epsilon: 'ε', theta: 'θ',
  lambda: 'λ', mu: 'μ', pi: 'π', rho: 'ρ', sigma: 'σ', Sigma: 'Σ', sum: 'Σ', tau: 'τ', omega: 'ω',
}
const TEX_CMD_RE = /\\([A-Za-z]+)(?![A-Za-z])/g

function texToText(s: string): string {
  return s
    .replace(/\\(?:text|mathrm|textbf|mathbf)\{([^{}]*)\}/g, '$1')
    .replace(/\\([%$&#_])/g, '$1')
    .replace(/\\[,;: ]/g, ' ')
    .replace(TEX_CMD_RE, (m, name: string) => TEX_SYMBOLS[name] ?? m)
}

export function plainMath(text: string): string {
  return text
    .replace(/\$(\S(?:[^$\n]*\S)?)\$/g, (m, inner: string) => {
      if (!inner.includes('\\')) return m
      const out = texToText(inner)
      return /\\[A-Za-z]/.test(out) ? m : out.replace(/[{}]/g, '')
    })
    .replace(TEX_CMD_RE, (m, name: string) => TEX_SYMBOLS[name] ?? m)
}

// Reference numbers ("...text.[1][2]" or "[1, 2]") point into the numbered source list at the
// end of the same message (its last top-level <ol>); each becomes a superscript that scrolls there.
const CITE_RE = /\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]/g

export function linkCitations(el: HTMLElement): void {
  const refs = [...el.querySelectorAll(':scope > ol')].pop()
  if (!refs) return
  const count = refs.children.length
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) =>
      refs.contains(node) ||
      node.parentElement?.closest('a, code, pre') ||
      !/\[\d/.test((node as Text).data)
        ? NodeFilter.FILTER_REJECT
        : NodeFilter.FILTER_ACCEPT,
  })
  const nodes: Text[] = []
  while (walker.nextNode()) nodes.push(walker.currentNode as Text)
  for (const node of nodes) {
    const frag = document.createDocumentFragment()
    let last = 0
    for (const match of node.data.matchAll(CITE_RE)) {
      const nums = match[1].split(',').map((n) => Number(n.trim()))
      if (nums.some((n) => n < 1 || n > count)) continue
      frag.append(node.data.slice(last, match.index))
      for (const n of nums) {
        const sup = document.createElement('sup')
        const a = document.createElement('a')
        a.className = 'cite'
        a.href = '#'
        a.dataset.ref = String(n)
        a.textContent = String(n)
        a.title = (refs.children[n - 1].textContent ?? '').trim()
        sup.append(a)
        frag.append(sup)
      }
      last = match.index + match[0].length
    }
    if (!last) continue
    frag.append(node.data.slice(last))
    node.replaceWith(frag)
  }
}
