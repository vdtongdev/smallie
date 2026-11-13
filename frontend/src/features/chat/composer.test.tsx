import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import { LanguageProvider } from '@/i18n'
import { en } from '@/i18n/en'
import Composer from './Composer'

test('blank and whitespace input is never passed to onSend', async () => {
  const onSend = vi.fn()
  render(
    <LanguageProvider>
      <Composer busy={false} awaiting={false} draft={null} onSend={onSend} />
    </LanguageProvider>,
  )
  const box = screen.getByPlaceholderText(en.placeholder)
  await userEvent.type(box, '   {Enter}')
  await userEvent.click(screen.getByRole('button', { name: en.send }))
  expect(onSend).not.toHaveBeenCalled()
  await userEvent.type(box, ' hi {Enter}')
  expect(onSend).toHaveBeenCalledWith('hi')
})
