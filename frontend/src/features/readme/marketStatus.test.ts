import { expect, test } from 'vitest'
import { en } from '@/i18n/en'
import { vi } from '@/i18n/vi'
import { marketStatusText } from './marketStatus'

const tEn = (k: keyof typeof en) => en[k]
const tVi = (k: keyof typeof vi) => vi[k]

test('null gives an empty string', () => {
  expect(marketStatusText(null, 'en', tEn)).toBe('')
})

test('loaded status', () => {
  expect(marketStatusText({ stocks: 200, last_trade_date: '2026-11-10', updated_at: null }, 'en', tEn)).toBe(
    'Currently loaded: 200 stocks, prices up to 2026-11-10.',
  )
})

test('not loaded', () => {
  expect(marketStatusText({ stocks: 0, last_trade_date: null, updated_at: null }, 'en', tEn)).toBe(en.marketEmpty)
})

test('updated_at appends the formatted time', () => {
  const updated = '2026-11-10T21:30:00Z'
  const when = new Date(updated).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' })
  const text = marketStatusText({ stocks: 200, last_trade_date: '2026-11-10', updated_at: updated }, 'en', tEn)
  expect(text.endsWith(en.marketUpdated.replace('{time}', when))).toBe(true)
})

test('Vietnamese uses vi strings and vi-VN time', () => {
  const updated = '2026-11-10T21:30:00Z'
  const when = new Date(updated).toLocaleString('vi-VN', { dateStyle: 'medium', timeStyle: 'short' })
  const text = marketStatusText({ stocks: 200, last_trade_date: '2026-11-10', updated_at: updated }, 'vi', tVi)
  expect(text).toBe(
    vi.marketStatus.replace('{stocks}', '200').replace('{date}', '2026-11-10') + ' ' + vi.marketUpdated.replace('{time}', when),
  )
})
