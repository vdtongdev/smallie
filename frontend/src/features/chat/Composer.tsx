import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useLang } from '@/i18n'

export type Draft = { text: string; id: number }

type Props = { busy: boolean; awaiting: boolean; draft: Draft | null; onSend(text: string): void }

export default function Composer({ busy, awaiting, draft, onSend }: Props) {
  const { t } = useLang()
  const [value, setValue] = useState('')
  const box = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (draft) {
      setValue(draft.text)
      box.current?.focus()
    }
  }, [draft])

  // The input grows with its text.
  useEffect(() => {
    const el = box.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [value])

  const submit = (e?: FormEvent) => {
    e?.preventDefault()
    const text = value.trim()
    if (!text || busy) return
    setValue('')
    onSend(text)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <form onSubmit={submit} className="flex items-end gap-2">
      <Textarea
        ref={box}
        rows={1}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={t(awaiting ? 'answerPlaceholder' : 'placeholder')}
        className="max-h-48"
      />
      <Button type="submit" disabled={busy}>
        {t('send')}
      </Button>
    </form>
  )
}
