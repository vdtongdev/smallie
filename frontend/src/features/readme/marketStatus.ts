import type { I18nKey } from '@/i18n/en'
import type { Lang } from '@/i18n'
import type { MarketStatus } from '@/lib/types'

export function marketStatusText(status: MarketStatus | null, lang: Lang, t: (k: I18nKey) => string): string {
  if (!status) return ''
  let text = status.last_trade_date
    ? t('marketStatus').replace('{stocks}', String(status.stocks)).replace('{date}', status.last_trade_date)
    : t('marketEmpty')
  if (status.updated_at) {
    const when = new Date(status.updated_at).toLocaleString(lang === 'vi' ? 'vi-VN' : 'en-US', {
      dateStyle: 'medium',
      timeStyle: 'short',
    })
    text += ' ' + t('marketUpdated').replace('{time}', when)
  }
  return text
}
