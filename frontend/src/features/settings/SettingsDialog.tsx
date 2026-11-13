import { useEffect, useState, type ReactNode } from 'react'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useLang } from '@/i18n'
import type { I18nKey } from '@/i18n/en'
import { modelOptions, PROFILES, unchanged, type Slots } from '@/lib/modelSettings'
import { PICKER_THEMES, setThemeName, type ThemeName } from '@/lib/themeName'
import { useModelSettings } from './useModelSettings'

type Props = { open: boolean; onOpenChange(open: boolean): void }
type Form = Slots & { key: string }
type Warnings = Record<'model' | 'small_model' | 'strong_model', string>

const NO_WARNINGS: Warnings = { model: '', small_model: '', strong_model: '' }
const selectClass =
  'h-9 w-full min-w-0 rounded-md border border-input bg-popover text-popover-foreground px-2.5 py-1 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50'
const THEME_LABEL: Record<string, I18nKey> = { shadcn: 'themeShadcn', chatroom: 'themeChatroom', light: 'themeLight' }

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  )
}

const Warning = ({ text }: { text: string }) => (
  <p role="status" className="min-h-0 text-sm text-destructive">
    {text}
  </p>
)

export default function SettingsDialog({ open, onOpenChange }: Props) {
  const { t } = useLang()
  const { loaded, hint, status, setStatus, models, save, clear } = useModelSettings(open)
  const [form, setForm] = useState<Form>({ ...loaded, key: '' })
  const [warn, setWarn] = useState<Warnings>(NO_WARNINGS)
  const [theme, setTheme] = useState<string | undefined>(() => document.documentElement.dataset.theme)

  // Every load (open, save, clear) resets the form; the key field always starts empty.
  useEffect(() => setForm({ ...loaded, key: '' }), [loaded])
  useEffect(() => {
    if (open) setWarn(NO_WARNINGS)
  }, [open])

  const set = (patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch }))
  const useSelect = models.length > 0
  const notListed = (id: string) => t('notListed').replace('{id}', id)
  const standard = useSelect ? form.model || models[0] : form.model.trim()
  const options = (value: string, blank?: string) => modelOptions(models, value, blank, notListed)

  const onSave = async () => {
    const key = form.key.trim()
    const base_url = form.base_url.trim()
    if (!base_url || !standard || !(key || hint)) return setStatus(t('settingsFieldsRequired'))
    const next: Slots = { base_url, model: standard, small_model: form.small_model, strong_model: form.strong_model, profile: form.profile }
    if (!key && unchanged(next, loaded)) return onOpenChange(false) // nothing changed since the last load
    try {
      const res = await save(next, key)
      const warnings = res.warnings ?? {}
      setWarn({
        model: res.warning || warnings.model || '',
        small_model: warnings.small_model || '',
        strong_model: warnings.strong_model || '',
      })
      if (res.warning || Object.keys(warnings).length) setStatus(`${t('saved')}. ${res.warning || ''}`.trim())
      else onOpenChange(false)
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err))
    }
  }

  const pickTheme = (name: ThemeName) => {
    setThemeName(name)
    setTheme(name)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby={undefined}>
        <DialogTitle>{t('settings')}</DialogTitle>
        <p role="status" className="text-sm text-muted-foreground">
          {status}
        </p>
        <Field label={t('apiKey')} htmlFor="settingsKey">
          <Input id="settingsKey" type="password" autoComplete="off" value={form.key} placeholder={hint} onChange={(e) => set({ key: e.target.value })} />
        </Field>
        <Field label={t('apiEndpoint')} htmlFor="settingsUrl">
          <Input id="settingsUrl" type="text" autoComplete="off" value={form.base_url} onChange={(e) => set({ base_url: e.target.value })} />
        </Field>

        <h3 className="font-medium">{t('profileGroup')}</h3>
        <div role="radiogroup" aria-label={t('profileGroup')} className="grid gap-2 sm:grid-cols-3">
          {PROFILES.map((p) => (
            <label key={p} className="flex cursor-pointer flex-col gap-1 rounded-md border p-3 has-[:checked]:border-primary has-[:checked]:bg-muted">
              <input type="radio" name="settingsProfile" value={p} checked={form.profile === p} onChange={() => set({ profile: p })} />
              <strong>{t(`${p}Profile` as I18nKey)}</strong>
              <span className="text-xs">{t(`${p}Tag` as I18nKey)}</span>
              <span className="text-muted-foreground">{t(`${p}Line` as I18nKey)}</span>
            </label>
          ))}
        </div>
        <p className="text-muted-foreground">{t('difficultyIntro')}</p>
        <ul className="list-disc pl-5 text-muted-foreground">
          <li><strong>{t('easy')}</strong>: {t('easyHelp')}</li>
          <li><strong>{t('medium')}</strong>: {t('mediumHelp')}</li>
          <li><strong>{t('hard')}</strong>: {t('hardHelp')}</li>
        </ul>

        <h3 className="font-medium">{t('modelsGroup')}</h3>
        <Field label={t('standardModel')} htmlFor="settingsModel">
          {useSelect ? (
            <select id="settingsModel" className={selectClass} value={standard} onChange={(e) => set({ model: e.target.value })}>
              {options(form.model).map((o) => (
                <option key={o.value} value={o.value} className="bg-popover text-popover-foreground">{o.label}</option>
              ))}
            </select>
          ) : (
            <Input id="settingsModel" type="text" autoComplete="off" value={form.model} onChange={(e) => set({ model: e.target.value })} />
          )}
          <Warning text={warn.model} />
        </Field>
        {(['small_model', 'strong_model'] as const).map((slot) => (
          <Field key={slot} label={t(slot === 'small_model' ? 'smallModel' : 'strongModel')} htmlFor={`settings-${slot}`}>
            <select id={`settings-${slot}`} className={selectClass} value={form[slot]} onChange={(e) => set({ [slot]: e.target.value })}>
              {options(form[slot], t('sameAsStandard')).map((o) => (
                <option key={o.value} value={o.value} className="bg-popover text-popover-foreground">{o.label}</option>
              ))}
            </select>
            <Warning text={warn[slot]} />
          </Field>
        ))}

        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr>
                <th>{t('colProvider')}</th>
                <th>{t('colKeyPage')}</th>
                <th>{t('apiEndpoint')}</th>
                <th>{t('model')}</th>
              </tr>
            </thead>
            <tbody>
              <tr><td>{t('provDeepseek')}</td><td>platform.deepseek.com</td><td>https://api.deepseek.com/v1</td><td>deepseek-chat</td></tr>
              <tr><td>OpenAI</td><td>platform.openai.com</td><td>https://api.openai.com/v1</td><td>gpt-4o-mini</td></tr>
              <tr><td>OpenRouter</td><td>openrouter.ai</td><td>https://openrouter.ai/api/v1</td><td>{t('modelYourChoice')}</td></tr>
              <tr><td>Alibaba DashScope</td><td>dashscope.aliyun.com</td><td>https://dashscope-intl.aliyuncs.com/compatible-mode/v1</td><td>qwen-plus</td></tr>
            </tbody>
          </table>
        </div>
        <p className="text-muted-foreground">{t('anyOpenAI')}</p>

        <h3 className="font-medium">{t('themeGroup')}</h3>
        <div role="radiogroup" aria-label={t('themeGroup')} className="flex flex-wrap gap-4">
          {PICKER_THEMES.map((name) => (
            <label key={name} className="flex cursor-pointer items-center gap-2">
              <input type="radio" name="settingsTheme" value={name} checked={theme === name} onChange={() => pickTheme(name)} />
              {t(THEME_LABEL[name])}
            </label>
          ))}
        </div>

        <div className="flex justify-end gap-2">
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button type="button" variant="outline">{t('clear')}</Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogTitle>{t('confirmClearSettings')}</AlertDialogTitle>
              <AlertDialogFooter>
                <AlertDialogCancel>{t('cancel')}</AlertDialogCancel>
                <AlertDialogAction onClick={() => void clear()}>{t('clear')}</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
          <Button type="button" onClick={() => void onSave()}>{t('save')}</Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
