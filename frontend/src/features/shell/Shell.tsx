import { useState } from 'react'
import { DocumentsPanel } from '@/features/documents/DocumentsPanel'
import ChatView from '@/features/chat/ChatView'
import type { Draft } from '@/features/chat/Composer'
import { useChat } from '@/features/chat/useChat'
import ReadmeDialog from '@/features/readme/ReadmeDialog'
import Sidebar from '@/features/sidebar/Sidebar'
import Header from './Header'

export default function Shell() {
  const [readmeOpen, setReadmeOpen] = useState(false)
  // Set by Readme examples; the composer fills its input from it.
  const [draft, setDraft] = useState<Draft | null>(null)
  const chat = useChat()
  return (
    <div className="flex h-screen flex-col">
      <Header onReadme={() => setReadmeOpen(true)} />
      <div className="flex min-h-0 flex-1">
        <Sidebar
          conversations={chat.conversations}
          currentId={chat.currentId}
          busy={chat.busy}
          teamMode={chat.kind === 'team'}
          onTeamMode={chat.setTeamMode}
          onNew={chat.newChat}
          onOpen={(id) => void chat.open(id)}
          onRemove={(id) => void chat.remove(id)}
          usageKey={chat.sent}
        />
        <main className="flex min-h-0 min-w-0 flex-1 flex-col gap-2 p-2">
          <DocumentsPanel />
          {/* Settings arrive in M4; until then the button does nothing. */}
          <ChatView chat={chat} draft={draft} onOpenSettings={() => {}} />
        </main>
      </div>
      <ReadmeDialog
        open={readmeOpen}
        onOpenChange={setReadmeOpen}
        onExample={(text) => setDraft((d) => ({ text, id: (d?.id ?? 0) + 1 }))}
      />
    </div>
  )
}
