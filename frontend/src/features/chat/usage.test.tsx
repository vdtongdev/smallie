import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { en } from '@/i18n/en'
import { LanguageProvider } from '@/i18n'
import type { Usage } from '@/lib/types'
import UsageDialog from './UsageDialog'

const show = (usage: Usage | null) =>
  render(
    <LanguageProvider>
      <UsageDialog usage={usage} open onOpenChange={() => {}} />
    </LanguageProvider>,
  )

const chat = { input_tokens: 1000, output_tokens: 200, cached_tokens: 500 }

describe('UsageDialog (app.js renderUsage)', () => {
  it('shows reply and chat sections', () => {
    show({ reply: { input_tokens: 100, output_tokens: 20, cached_tokens: 50 }, chat })
    expect(screen.getByText(en.usageReply)).toBeInTheDocument()
    expect(screen.getByText(en.usageChat)).toBeInTheDocument()
    expect(screen.getByText('in 100 · out 20 · cached 50 (50%)')).toBeInTheDocument()
    expect(screen.getByText('in 1,000 · out 200 · cached 500 (50%)')).toBeInTheDocument()
  })
  it('no reply section when reply is null', () => {
    show({ reply: null, chat })
    expect(screen.queryByText(en.usageReply)).not.toBeInTheDocument()
  })
  it('cached null reads n/a', () => {
    show({ reply: null, chat: { ...chat, cached_tokens: null } })
    expect(screen.getByText('in 1,000 · out 200 · cached n/a')).toBeInTheDocument()
  })
  it('zero input has no rate', () => {
    show({ reply: null, chat: { input_tokens: 0, output_tokens: 5, cached_tokens: 0 } })
    expect(screen.getByText('in 0 · out 5 · cached 0')).toBeInTheDocument()
  })
  it('per-model rows sorted by most input, with call plurals and n/a', () => {
    show({
      reply: null,
      chat,
      chat_by_model: {
        small: { input_tokens: 10, output_tokens: 1, cached_tokens: 5, model_calls: 1 },
        big: { input_tokens: 990, output_tokens: 1, cached_tokens: null, model_calls: 3 },
      },
    })
    const rows = screen.getAllByText(/^(small|big):/)
    expect(rows.map((r) => r.textContent)).toEqual([
      'big: 3 calls · in 990 · cached n/a',
      'small: 1 call · in 10 · cached 5 (50%)',
    ])
  })
  it('cache note only when chat cached is 0 with input', () => {
    const { unmount } = show({ reply: null, chat: { ...chat, cached_tokens: 0 } })
    expect(screen.getByText(en.usageCacheNote)).toBeInTheDocument()
    unmount()
    show({ reply: null, chat })
    expect(screen.queryByText(en.usageCacheNote)).not.toBeInTheDocument()
  })
})
