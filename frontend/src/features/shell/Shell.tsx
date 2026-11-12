import { useState } from 'react'
import ReadmeDialog from '@/features/readme/ReadmeDialog'
import { useLang } from '@/i18n'
import Header from './Header'

export default function Shell() {
  const { t } = useLang()
  const [readmeOpen, setReadmeOpen] = useState(false)
  // Set by Readme examples; the composer (M2) will read it.
  const [, setDraft] = useState('')
  return (
    <>
      <Header onReadme={() => setReadmeOpen(true)} />
      <main className="p-4 text-muted-foreground">{t('welcome')}</main>
      <ReadmeDialog open={readmeOpen} onOpenChange={setReadmeOpen} onExample={setDraft} />
    </>
  )
}
