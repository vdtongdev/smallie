import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { en, type I18nKey } from '@/i18n/en'
import { useLang } from '@/i18n'
import { useAuth } from './AuthProvider'

export default function AuthScreen() {
  const [signup, setSignup] = useState(false)
  return (
    <div className="flex justify-center p-4">
      {signup ? <SignupCard onSwitch={() => setSignup(false)} /> : <LoginCard onSwitch={() => setSignup(true)} />}
    </div>
  )
}

function LoginCard({ onSwitch }: { onSwitch: () => void }) {
  const { t } = useLang()
  const { login } = useAuth()
  const [failed, setFailed] = useState(false)
  const first = useRef<HTMLInputElement>(null)
  useEffect(() => first.current?.focus(), [])

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setFailed(false)
    const form = new FormData(e.currentTarget)
    if (!(await login(String(form.get('username')), String(form.get('password'))))) setFailed(true)
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle>smallie</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="flex flex-col gap-3">
          <Label htmlFor="loginUsername">{t('username')}</Label>
          <Input ref={first} id="loginUsername" name="username" autoComplete="username" autoCapitalize="none" maxLength={64} required />
          <Label htmlFor="loginPassword">{t('password')}</Label>
          <Input id="loginPassword" name="password" type="password" autoComplete="current-password" maxLength={128} required />
          {failed && <Alert variant="destructive">{t('wrongLogin')}</Alert>}
          <Button type="submit">{t('login')}</Button>
          <Button type="button" variant="link" onClick={onSwitch}>
            {t('noAccount')}
          </Button>
        </form>
      </CardContent>
    </Card>
  )
}

function SignupCard({ onSwitch }: { onSwitch: () => void }) {
  const { t } = useLang()
  const { signup } = useAuth()
  const [error, setError] = useState<string | null>(null)
  const first = useRef<HTMLInputElement>(null)
  useEffect(() => first.current?.focus(), [])

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setError(null)
    const form = new FormData(e.currentTarget)
    setError(await signup(String(form.get('name')), String(form.get('username')), String(form.get('password'))))
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle>{t('createAccount')}</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="flex flex-col gap-3">
          <Label htmlFor="signupName">{t('name')}</Label>
          <Input ref={first} id="signupName" name="name" autoComplete="name" maxLength={80} required />
          <Label htmlFor="signupUsername">{t('username')}</Label>
          <Input
            id="signupUsername"
            name="username"
            autoComplete="username"
            autoCapitalize="none"
            required
            minLength={3}
            maxLength={32}
            pattern="[A-Za-z0-9_\-]{3,32}"
          />
          <small className="text-muted-foreground">{t('usernameHint')}</small>
          <Label htmlFor="signupPassword">{t('password')}</Label>
          <Input id="signupPassword" name="password" type="password" autoComplete="new-password" minLength={8} maxLength={128} required />
          <small className="text-muted-foreground">{t('passwordHint')}</small>
          {error && <Alert variant="destructive">{error in en ? t(error as I18nKey) : error}</Alert>}
          <Button type="submit">{t('createAccount')}</Button>
          <Button type="button" variant="link" onClick={onSwitch}>
            {t('haveAccount')}
          </Button>
        </form>
      </CardContent>
    </Card>
  )
}
