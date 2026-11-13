import { render } from '@testing-library/react'
import { expect, test } from 'vitest'
import { AuthProvider } from '@/features/auth/AuthProvider'
import { LanguageProvider } from '@/i18n'
import Header from './Header'

test('the header has the header colour and a border line under it', () => {
  const { container } = render(
    <LanguageProvider>
      <AuthProvider>
        <Header />
      </AuthProvider>
    </LanguageProvider>,
  )
  const header = container.querySelector('header')!
  expect(header).toHaveClass('bg-header', 'border-b', 'border-border')
})
