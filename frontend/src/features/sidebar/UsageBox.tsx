import { useEffect, useState } from 'react'
import { useLang } from '@/i18n'
import { api } from '@/lib/api'

type Totals = { input_tokens: number; output_tokens: number; cost: number }

/** Tokens and cost of all this account's chats; hidden until loaded and when the load fails. */
export default function UsageBox({ refreshKey }: { refreshKey: number }) {
  const { t, lang } = useLang()
  const [usage, setUsage] = useState<Totals | null>(null)

  useEffect(() => {
    let cancelled = false
    api<Totals>('/usage')
      .then((u) => {
        if (!cancelled) setUsage(u)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [refreshKey, lang])

  if (!usage) return null
  const locale = lang === 'vi' ? 'vi-VN' : 'en-US'
  const n = (x: number) => x.toLocaleString(locale)
  return (
    <div className="mt-auto flex flex-col gap-0.5 border-t pt-2 text-xs text-muted-foreground">
      <div className="font-medium">{t('usage')}</div>
      <div title={t('usageDetail').replace('{input}', n(usage.input_tokens)).replace('{output}', n(usage.output_tokens))}>
        {t('usageTokens').replace('{n}', n(usage.input_tokens + usage.output_tokens))}
      </div>
      <div>{t('usageCost').replace('{cost}', usage.cost.toFixed(usage.cost < 0.01 ? 6 : 4))}</div>
    </div>
  )
}
