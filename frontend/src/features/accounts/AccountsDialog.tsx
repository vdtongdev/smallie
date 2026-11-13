import { useCallback, useEffect, useState } from 'react'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAuth } from '@/features/auth/AuthProvider'
import { useLang } from '@/i18n'
import { api } from '@/lib/api'

type Account = { id: string; name?: string; username: string; is_admin: boolean }
type Props = { open: boolean; onOpenChange(open: boolean): void }

const message = (err: unknown) => (err instanceof Error ? err.message : String(err))

/** The admin's account list: reset a password, delete an account (app.js 1058-1110). */
export default function AccountsDialog({ open, onOpenChange }: Props) {
  const { t } = useLang()
  const { me } = useAuth()
  const [items, setItems] = useState<Account[]>([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [resetting, setResetting] = useState<Account | null>(null)
  const [deleting, setDeleting] = useState<Account | null>(null)
  const [password, setPassword] = useState('')
  const [resetError, setResetError] = useState('')

  const load = useCallback(async (keepError = false) => {
    try {
      setItems(await api<Account[]>('/admin/accounts'))
      if (!keepError) setError('')
    } catch (err) {
      setItems([])
      setError(message(err))
    }
  }, [])

  useEffect(() => {
    if (open) {
      setNotice('')
      void load()
    }
  }, [open, load])

  const used = items.filter((a) => !a.is_admin).length

  const openReset = (a: Account) => {
    setPassword('')
    setResetError('')
    setResetting(a)
  }

  const submitReset = async () => {
    if (!resetting) return
    if (password.length < 8) return setResetError(t('passwordHint'))
    try {
      await api(`/admin/accounts/${resetting.id}/password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      setPassword('') // sent once, never kept
      setResetting(null)
      setNotice(t('passwordChanged'))
    } catch (err) {
      setResetError(message(err))
    }
  }

  const confirmDelete = async () => {
    if (!deleting) return
    const id = deleting.id
    setDeleting(null)
    setNotice('')
    try {
      await api(`/admin/accounts/${id}`, { method: 'DELETE' })
    } catch (err) {
      setError(message(err))
    }
    await load(true) // keep a delete error on screen after the reload
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent aria-describedby={undefined}>
          <DialogTitle>{t('accountsTitle')}</DialogTitle>
          <p className="text-sm text-muted-foreground">
            {t('accountCount').replace('{count}', String(used)).replace('{max}', String(me?.max_accounts ?? ''))}
          </p>
          {error && <Alert variant="destructive">{error}</Alert>}
          {notice && <p role="status" className="text-sm">{notice}</p>}
          <ul className="flex flex-col gap-2">
            {items.map((a) => (
              <li key={a.id} className="flex items-center justify-between gap-2">
                <span>{`${a.name || a.username} (@${a.username})` + (a.is_admin ? ` · ${t('adminBadge')}` : '')}</span>
                {!a.is_admin && (
                  <span className="flex gap-2">
                    <Button type="button" variant="outline" onClick={() => openReset(a)}>{t('resetPassword')}</Button>
                    <Button type="button" variant="outline" onClick={() => setDeleting(a)}>{t('delete')}</Button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </DialogContent>
      </Dialog>

      <Dialog open={resetting !== null} onOpenChange={(o) => !o && setResetting(null)}>
        <DialogContent aria-describedby={undefined}>
          <DialogTitle>{t('resetPassword')}</DialogTitle>
          <div className="grid gap-1.5">
            <Label htmlFor="accountsNewPassword">{t('newPassword').replace('{user}', resetting?.username ?? '')}</Label>
            <Input
              id="accountsNewPassword"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {resetError && <Alert variant="destructive">{resetError}</Alert>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setResetting(null)}>{t('cancel')}</Button>
            <Button type="button" onClick={() => void submitReset()}>{t('save')}</Button>
          </div>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogTitle>{t('confirmDeleteAccount').replace('{user}', deleting?.username ?? '')}</AlertDialogTitle>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('cancel')}</AlertDialogCancel>
            <AlertDialogAction onClick={() => void confirmDelete()}>{t('delete')}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
