import { XIcon } from 'lucide-react'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { useLang } from '@/i18n'
import type { Conversation } from '@/lib/types'
import UsageBox from './UsageBox'

type Props = {
  conversations: Conversation[]
  currentId: string | null
  busy: boolean
  teamMode: boolean
  onTeamMode(on: boolean): void
  onNew(): void
  onOpen(id: string): void
  onRemove(id: string): void
  /** Changes after every finished send, so the usage box reloads. */
  usageKey?: number
  onOpenSettings?(): void
}

export default function Sidebar({ conversations, currentId, busy, teamMode, onTeamMode, onNew, onOpen, onRemove, usageKey = 0, onOpenSettings }: Props) {
  const { t } = useLang()
  return (
    <aside className="flex w-64 shrink-0 flex-col gap-2 overflow-y-auto border-r p-2">
      <Button type="button" onClick={onNew} disabled={busy}>
        {t('newChat')}
      </Button>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={teamMode}
          disabled={busy || currentId !== null}
          onChange={(e) => onTeamMode(e.target.checked)}
        />
        {t('teamMode')}
      </label>
      <ul className="flex flex-col gap-1">
        {conversations.length === 0 && <li className="text-sm text-muted-foreground">{t('noConversations')}</li>}
        {conversations.map((c) => (
          <li
            key={c.id}
            aria-current={c.id === currentId ? 'true' : undefined}
            className={`flex items-center gap-1 rounded-md px-2 py-1 text-sm ${c.id === currentId ? 'bg-muted' : ''}`}
          >
            {c.kind === 'team' && <span title={t('teamMode')}>👥</span>}
            <button
              type="button"
              title={c.title}
              disabled={busy}
              onClick={() => onOpen(c.id)}
              className="min-w-0 flex-1 truncate text-left disabled:opacity-60"
            >
              {c.title}
            </button>
            <AlertDialog>
              <AlertDialogTrigger aria-label={`${t('delete')} ${c.title}`} title={t('delete')}>
                <XIcon className="size-4" />
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogTitle>{t('confirmDelete')}</AlertDialogTitle>
                <AlertDialogFooter>
                  <AlertDialogCancel>{t('cancel')}</AlertDialogCancel>
                  <AlertDialogAction onClick={() => onRemove(c.id)}>{t('delete')}</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </li>
        ))}
      </ul>
      <Button type="button" variant="outline" onClick={onOpenSettings}>
        {t('settings')}
      </Button>
      <UsageBox refreshKey={usageKey} />
    </aside>
  )
}
