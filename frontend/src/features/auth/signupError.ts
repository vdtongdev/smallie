import type { I18nKey } from '@/i18n/en'

// Port of signupError in app/static/app.js.
export function signupErrorKey(status: number, detail: unknown): I18nKey | string {
  if (status === 409) return 'err_taken'
  if (status === 403) return 'err_limit'
  if (Array.isArray(detail)) {
    const first = detail[0] as { loc?: unknown[]; msg?: unknown } | undefined
    const field = first?.loc?.at(-1)
    if (field === 'username') return String(first?.msg).includes('reserved') ? 'err_reserved' : 'usernameHint'
    if (field === 'password') return 'passwordHint'
    return (first?.msg as string | undefined) || 'Error'
  }
  return (detail as string | undefined) || 'Error'
}
