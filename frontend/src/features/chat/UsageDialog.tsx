import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { useLang } from '@/i18n'
import type { I18nKey } from '@/i18n/en'
import type { ModelUsage, Usage, UsagePart } from '@/lib/types'

type Props = { usage: Usage | null; open: boolean; onOpenChange(o: boolean): void }

// Port of app.js renderUsage: cached_tokens null means the provider did not say (n/a, not 0).
export default function UsageDialog({ usage, open, onOpenChange }: Props) {
  const { lang, t } = useLang()
  const n = (x: number) => x.toLocaleString(lang === 'vi' ? 'vi-VN' : 'en-US')
  const rate = (cached: number | null, input: number) => (input ? Math.round((100 * (cached ?? 0)) / input) : 0)

  const modelRows = (byModel?: Record<string, ModelUsage>) =>
    Object.entries(byModel || {})
      .sort((a, b) => b[1].input_tokens - a[1].input_tokens)
      .map(([name, m]) => (
        <div key={name} className="text-sm text-muted-foreground">
          {t(m.cached_tokens == null ? 'usageModelUnknown' : 'usageModelRow')
            .replace('{model}', name)
            .replace('{calls}', t(m.model_calls === 1 ? 'usageCall' : 'usageCalls').replace('{n}', n(m.model_calls)))
            .replace('{in}', n(m.input_tokens))
            .replace('{cached}', n(m.cached_tokens ?? 0))
            .replace('{rate}', String(rate(m.cached_tokens, m.input_tokens)))}
        </div>
      ))

  const section = (label: I18nKey, p: UsagePart, byModel?: Record<string, ModelUsage>) => {
    const key = p.cached_tokens == null ? 'usageLineUnknown' : p.input_tokens ? 'usageLine' : 'usageLineNoRate'
    return (
      <div key={label} className="space-y-1">
        <h3 className="font-medium">{t(label)}</h3>
        <div>
          {t(key)
            .replace('{in}', n(p.input_tokens))
            .replace('{out}', n(p.output_tokens))
            .replace('{cached}', n(p.cached_tokens ?? 0))
            .replace('{rate}', String(rate(p.cached_tokens, p.input_tokens)))}
        </div>
        {modelRows(byModel)}
      </div>
    )
  }

  const chat = usage?.chat
  const noCache = !!chat && chat.cached_tokens === 0 && chat.input_tokens > 0

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby={undefined}>
        <DialogTitle>{t('usage')}</DialogTitle>
        {usage && chat && (
          <div className="space-y-3">
            {usage.reply && section('usageReply', usage.reply, usage.reply_by_model)}
            {section('usageChat', chat, usage.chat_by_model)}
            {noCache && <div className="text-xs text-muted-foreground">{t('usageCacheNote')}</div>}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
