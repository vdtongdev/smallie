import { XIcon } from 'lucide-react'
import { useLang } from '@/i18n'

export type Chip = {
  key: string
  filename: string
  preview: string
  state: 'reading' | 'failed' | 'unread' | 'ready'
  error?: string | null
}

type Props = { chips: Chip[]; onRemove(key: string): void }

/** The images attached to the message being written, each with its status. */
export default function AttachmentChips({ chips, onRemove }: Props) {
  const { t } = useLang()
  return (
    <ul className="flex flex-wrap gap-2">
      {chips.map((c) => (
        <li key={c.key} className="relative flex max-w-56 items-center gap-2 rounded-md border bg-background p-1 pr-7 text-xs">
          <img src={c.preview} alt={c.filename} className="size-10 shrink-0 rounded object-cover" />
          <span className="min-w-0">
            <span className="block truncate">{c.filename}</span>
            {c.state === 'reading' && <span className="block text-muted-foreground">{t('attachReading')}</span>}
            {c.state === 'failed' && <span className="block text-destructive">{c.error}</span>}
            {c.state === 'unread' && <span className="block text-destructive">{t('attachUnread')}</span>}
          </span>
          <button
            type="button"
            aria-label={t('attachRemove')}
            onClick={() => onRemove(c.key)}
            className="absolute top-1 right-1 rounded p-0.5 hover:bg-muted"
          >
            <XIcon className="size-3" />
          </button>
        </li>
      ))}
    </ul>
  )
}
