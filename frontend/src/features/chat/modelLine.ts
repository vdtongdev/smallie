// Port of app.js modelLine / fallbackLine (lines 379-406): which model handled a reply, and why.
import type { Lang } from '@/i18n'

export type Tr = {
  t(key: string): string
  lang: Lang
  agentLabel?(agent: string): string
}
export type ModelTrace = {
  tier?: string
  difficulty?: string
  profile?: string
  usage?: { model_calls?: number; input_tokens?: number; output_tokens?: number; cost?: number | null }
}
export type FallbackInfo = { from_model: string; to_model?: string; category: string }

const nf = (n: number, lang: Lang) => Number(n).toLocaleString(lang === 'vi' ? 'vi-VN' : 'en-US')
const tierWord = (tier: string, tr: Tr) => tr.t(({ small: 'smallModel', strong: 'strongModel' } as Record<string, string>)[tier] || 'standardModel')

export function modelLine(m: ModelTrace | null | undefined, tr: Tr, agent?: string): string {
  if (!m?.tier) return ''
  const { t, lang } = tr
  const parts: string[] = []
  if (agent) parts.push(tr.agentLabel ? tr.agentLabel(agent) : agent)
  if (m.difficulty) parts.push(agent ? t(m.difficulty + 'Task') : `${t(m.difficulty)} (1v1)`)
  parts.push(`${t(m.profile + 'Profile')} → ${tierWord(m.tier, tr).toLowerCase()}`)
  const u = m.usage
  if (u?.model_calls) {
    parts.push(t('callsN').replace('{n}', nf(u.model_calls, lang)))
    parts.push(t('tokensN').replace('{n}', nf((u.input_tokens || 0) + (u.output_tokens || 0), lang)))
    if (u.cost != null) parts.push(`$${u.cost.toFixed(u.cost < 0.01 ? 6 : 4)}`)
  }
  return parts.join(' · ')
}

export function fallbackLine(f: FallbackInfo, tr: Tr, tier?: string): string {
  const { t } = tr
  const why = t('fb_' + f.category)
  const reason = why === 'fb_' + f.category ? f.category : why
  if (f.to_model) {
    const from = tier && tier !== 'standard' ? `${tierWord(tier, tr).toLowerCase()} ${f.from_model}` : f.from_model
    return t('fallbackLineTo').replace('{model}', from).replace('{why}', reason).replace('{to}', f.to_model)
  }
  const who = tier && tier !== 'standard' ? tierWord(tier, tr) : f.from_model
  return t('fallbackLine').replace('{model}', who).replace('{why}', reason)
}
