import { Button } from '@/components/ui/button'
import { useLang } from '@/i18n'

export default function App() {
  const { lang, setLang, t } = useLang()
  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="flex items-center justify-between p-4">
        <h1 className="text-lg font-semibold">smallie</h1>
        <Button variant="outline" onClick={() => setLang(lang === 'en' ? 'vi' : 'en')}>
          {lang === 'en' ? 'Tiếng Việt' : 'English'}
        </Button>
      </header>
      <main className="p-4 text-muted-foreground">{t('welcome')}</main>
    </div>
  )
}
