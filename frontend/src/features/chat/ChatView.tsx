import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { useLang } from '@/i18n'
import type { Usage } from '@/lib/types'
import ActivityDialog from './ActivityDialog'
import Composer, { type Draft } from './Composer'
import MessageList from './MessageList'
import UsageDialog from './UsageDialog'
import { useAgents } from './useAgents'
import type { Chat } from './useChat'

type Props = {
  chat: Chat
  draft: Draft | null
  onOpenSettings(): void
}

// Old renderUsage: no button when there is no usage, or all of it is zero and there is no reply.
const hasUsage = (u: Usage | null) => !!u && (!!u.reply || u.chat.input_tokens > 0 || u.chat.output_tokens > 0)

export default function ChatView({ chat, draft, onOpenSettings }: Props) {
  const { t } = useLang()
  const labels = useAgents()
  const [usageOpen, setUsageOpen] = useState(false)
  // The index, not the turn: every stream event makes a new turn object, and the dialog follows the live one.
  // It belongs to one chat: another chat's turns, or a shorter list, close it.
  const [activity, setActivity] = useState<{ idx: number; chatId: string | null } | null>(null)
  const activityTurn = activity && activity.chatId === chat.currentId ? chat.turns[activity.idx] : undefined
  useEffect(() => {
    if (activity && !activityTurn) setActivity(null)
  }, [activity, activityTurn])
  const showUsage = hasUsage(chat.usage)
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <MessageList
        turns={chat.turns}
        loadError={chat.loadError}
        labels={labels}
        onOpenActivity={(turn) => {
          const idx = chat.turns.indexOf(turn)
          if (idx >= 0) setActivity({ idx, chatId: chat.currentId })
        }}
        onOpenSettings={onOpenSettings}
      />
      <Composer busy={chat.busy} awaiting={!!chat.turns.at(-1)?.pending} draft={draft} onSend={(text) => void chat.send(text)} />
      <div className="flex justify-end">
        {showUsage && (
          <Button type="button" variant="ghost" size="sm" onClick={() => setUsageOpen(true)}>
            {t('usage')}
          </Button>
        )}
      </div>
      {activityTurn && (
        <ActivityDialog turn={activityTurn} open onOpenChange={(o) => !o && setActivity(null)} />
      )}
      <UsageDialog usage={chat.usage} open={usageOpen && showUsage} onOpenChange={setUsageOpen} />
    </div>
  )
}
