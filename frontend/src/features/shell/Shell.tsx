import { useState } from 'react'
import AccountsDialog from '@/features/accounts/AccountsDialog'
import { DocumentsPanel } from '@/features/documents/DocumentsPanel'
import ChatView from '@/features/chat/ChatView'
import type { Draft } from '@/features/chat/Composer'
import { useChat } from '@/features/chat/useChat'
import ReadmeDialog from '@/features/readme/ReadmeDialog'
import SettingsDialog from '@/features/settings/SettingsDialog'
import Sidebar from '@/features/sidebar/Sidebar'
import Header from './Header'

export default function Shell() {
  const [readmeOpen, setReadmeOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [accountsOpen, setAccountsOpen] = useState(false)
  // Set by Readme examples; the composer fills its input from it.
  const [draft, setDraft] = useState<Draft | null>(null)
  const chat = useChat()
  return (
    <div className="flex h-screen flex-col">
      <Header onReadme={() => setReadmeOpen(true)} onAccounts={() => setAccountsOpen(true)} />
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
          onOpenSettings={() => setSettingsOpen(true)}
        />
        <main className="flex min-h-0 min-w-0 flex-1 flex-col gap-2 p-2">
          <DocumentsPanel />
          <ChatView chat={chat} draft={draft} onOpenSettings={() => setSettingsOpen(true)} />
        </main>
      </div>
      {/* Always mounted: it loads the saved settings and the provider's model list in the background after login. */}
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
      <AccountsDialog open={accountsOpen} onOpenChange={setAccountsOpen} />
      <ReadmeDialog
        open={readmeOpen}
        onOpenChange={setReadmeOpen}
        onExample={(text) => setDraft((d) => ({ text, id: (d?.id ?? 0) + 1 }))}
      />
    </div>
  )
}
