import { describe, expect, it } from 'vitest'
import { en } from '@/i18n/en'
import { fallbackLine, modelLine } from './modelLine'

const tr = {
  t: (k: string) => (en as Record<string, string>)[k] ?? k,
  lang: 'en' as const,
  agentLabel: (a: string) => `<${a}>`,
}

describe('modelLine (app.js modelLine)', () => {
  it('is empty without a tier', () => {
    expect(modelLine(undefined, tr)).toBe('')
    expect(modelLine({ profile: 'balanced' }, tr)).toBe('')
  })
  it('1v1: difficulty in brackets, profile arrow tier', () => {
    expect(modelLine({ tier: 'small', difficulty: 'easy', profile: 'balanced' }, tr)).toBe(
      'easy (1v1) · Balanced → small model',
    )
  })
  it('without difficulty', () => {
    expect(modelLine({ tier: 'strong', profile: 'advanced' }, tr)).toBe('Advanced → strong model')
  })
  it('unknown tier reads as standard', () => {
    expect(modelLine({ tier: 'standard', profile: 'economy' }, tr)).toBe('Economy → standard model')
  })
  it('with an agent: label first and task wording', () => {
    expect(modelLine({ tier: 'small', difficulty: 'hard', profile: 'economy' }, tr, 'sql')).toBe(
      '<sql> · hard task · Economy → small model',
    )
  })
  it('usage adds calls, tokens and cost (6 decimals under a cent, else 4)', () => {
    const u = { model_calls: 2, input_tokens: 1000, output_tokens: 234, cost: 0.000123 }
    expect(modelLine({ tier: 'small', profile: 'balanced', usage: u }, tr)).toBe(
      'Balanced → small model · 2 calls · 1,234 tokens · $0.000123',
    )
    expect(modelLine({ tier: 'small', profile: 'balanced', usage: { ...u, cost: 0.5 } }, tr)).toBe(
      'Balanced → small model · 2 calls · 1,234 tokens · $0.5000',
    )
  })
  it('usage without cost, missing token fields, or zero calls', () => {
    expect(modelLine({ tier: 'small', profile: 'balanced', usage: { model_calls: 1 } }, tr)).toBe(
      'Balanced → small model · 1 calls · 0 tokens',
    )
    expect(modelLine({ tier: 'small', profile: 'balanced', usage: { model_calls: 0 } }, tr)).toBe(
      'Balanced → small model',
    )
  })
  it('formats numbers for vi', () => {
    const vi = { ...tr, lang: 'vi' as const, t: (k: string) => (k === 'tokensN' ? '{n} token' : (en as Record<string, string>)[k] ?? k) }
    expect(
      modelLine({ tier: 'small', profile: 'balanced', usage: { model_calls: 1, input_tokens: 1234567 } }, vi),
    ).toContain('1.234.567 token')
  })
})

describe('fallbackLine (app.js fallbackLine)', () => {
  it('with to_model, standard tier shows the plain model name', () => {
    expect(fallbackLine({ from_model: 'a/b', to_model: 'c/d', category: 'server_error' }, tr)).toBe(
      'a/b failed (server error), repeated on c/d',
    )
  })
  it('with to_model, non-standard tier prefixes the tier word', () => {
    expect(fallbackLine({ from_model: 'a/b', to_model: 'c/d', category: 'unsupported' }, tr, 'small')).toBe(
      'small model a/b failed (not supported), repeated on c/d',
    )
    expect(fallbackLine({ from_model: 'a/b', to_model: 'c/d', category: 'unsupported' }, tr, 'standard')).toBe(
      'a/b failed (not supported), repeated on c/d',
    )
  })
  it('without to_model uses the plain line; tier word replaces the model', () => {
    expect(fallbackLine({ from_model: 'a/b', category: 'model_not_found' }, tr)).toBe(
      'a/b failed (model not found), used standard',
    )
    expect(fallbackLine({ from_model: 'a/b', category: 'model_not_found' }, tr, 'strong')).toBe(
      'Strong model failed (model not found), used standard',
    )
  })
  it('unknown category is shown as is', () => {
    expect(fallbackLine({ from_model: 'a/b', category: 'weird' }, tr)).toBe('a/b failed (weird), used standard')
  })
})
