import { useEffect, useState } from 'react'
import { Dialog, DialogClose, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { useLang } from '@/i18n'
import { api } from '@/lib/api'
import type { MarketStatus } from '@/lib/types'
import { marketStatusText } from './marketStatus'
import ReadmeEn from './ReadmeEn'
import ReadmeVi from './ReadmeVi'

type Props = { open: boolean; onOpenChange(o: boolean): void; onExample(text: string): void }

export default function ReadmeDialog({ open, onOpenChange, onExample }: Props) {
  const { lang, t } = useLang()
  const [status, setStatus] = useState<MarketStatus | null>(null)

  useEffect(() => {
    if (!open) return
    let current = true
    api<MarketStatus>('/market/status')
      .then((s) => current && setStatus(s))
      .catch(() => current && setStatus(null))
    return () => {
      current = false
    }
  }, [open, lang])

  const choose = (text: string) => {
    onOpenChange(false)
    onExample(text)
  }
  const Readme = lang === 'vi' ? ReadmeVi : ReadmeEn

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false} aria-describedby={undefined} className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
        <DialogTitle className="sr-only">{t('readme')}</DialogTitle>
        <DialogClose className="absolute top-3 right-3 rounded px-2 text-lg leading-none" aria-label={t('close')}>
          ×
        </DialogClose>
        <Readme marketStatus={marketStatusText(status, lang, t)} onExample={choose} />
      </DialogContent>
    </Dialog>
  )
}
