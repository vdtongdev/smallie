import { Button } from '@/components/ui/button'
import { useAuth } from '@/features/auth/AuthProvider'
import { useLang } from '@/i18n'

export default function Header({ onReadme, onAccounts }: { onReadme?: () => void; onAccounts?: () => void }) {
  const { lang, setLang, t } = useLang()
  const { me, logout } = useAuth()
  return (
    <header className="flex items-center justify-between gap-2 border-b border-border bg-header p-4">
      <h1 className="text-lg font-semibold">smallie</h1>
      <div className="flex items-center gap-2">
        {me && <span title={`@${me.username}`}>{me.name}</span>}
        {me?.is_admin && onAccounts && (
          <Button variant="outline" onClick={onAccounts}>
            {t('accounts')}
          </Button>
        )}
        {me && onReadme && (
          <Button variant="outline" onClick={onReadme}>
            {t('readme')}
          </Button>
        )}
        <Button variant="outline" onClick={() => setLang(lang === 'en' ? 'vi' : 'en')}>
          {lang === 'en' ? 'Tiếng Việt' : 'English'}
        </Button>
        {me && (
          <Button variant="outline" onClick={() => void logout()}>
            {t('logout')}
          </Button>
        )}
      </div>
    </header>
  )
}
