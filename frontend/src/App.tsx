import { Button } from '@/components/ui/button'
import AuthScreen from '@/features/auth/AuthScreen'
import { useAuth } from '@/features/auth/AuthProvider'
import { useLang } from '@/i18n'

export default function App() {
  const { lang, setLang, t } = useLang()
  const { status, me, logout } = useAuth()
  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="flex items-center justify-between gap-2 p-4">
        <h1 className="text-lg font-semibold">smallie</h1>
        <div className="flex items-center gap-2">
          {me && <span title={`@${me.username}`}>{me.name}</span>}
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
      {status === 'out' && <AuthScreen />}
      {status === 'in' && <main className="p-4 text-muted-foreground">{t('welcome')}</main>}
    </div>
  )
}
