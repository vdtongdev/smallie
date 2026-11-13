import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { LanguageProvider } from '@/i18n'
import { newTurn } from '@/lib/turn'
import ActivityDialog from '@/features/chat/ActivityDialog'
import UsageDialog from '@/features/chat/UsageDialog'
import ReadmeDialog from '@/features/readme/ReadmeDialog'
import { AlertDialog, AlertDialogContent, AlertDialogTitle } from './alert-dialog'
import { Dialog, DialogContent, DialogTitle } from './dialog'

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ stocks: 1, last_trade_date: null, updated_at: null }), { status: 200 })))
})
afterEach(() => vi.unstubAllGlobals())

const WIDTH = 'w-[min(800px,calc(100vw-2rem))]'
const content = () => document.querySelector('[role="dialog"]')!

test('DialogContent is 800px wide at most and scrolls inside the viewport', () => {
  render(
    <Dialog open>
      <DialogContent aria-describedby={undefined}>
        <DialogTitle>t</DialogTitle>
      </DialogContent>
    </Dialog>,
  )
  const cls = content().className
  expect(cls).toContain(WIDTH)
  expect(cls).toContain('max-h-[calc(100vh-2rem)]')
  expect(cls).toContain('overflow-y-auto')
  expect(cls).not.toContain('sm:max-w-md')
})

test('a className from the caller merges with the shared classes', () => {
  render(
    <Dialog open>
      <DialogContent aria-describedby={undefined} className="bg-red-500">
        <DialogTitle>t</DialogTitle>
      </DialogContent>
    </Dialog>,
  )
  expect(content().className).toContain('bg-red-500')
  expect(content().className).toContain(WIDTH)
})

test('Readme, Activity and Usage popups use the shared width', () => {
  const { unmount: u1 } = render(
    <LanguageProvider>
      <ReadmeDialog open onOpenChange={() => {}} onExample={() => {}} />
    </LanguageProvider>,
  )
  expect(content().className).toContain(WIDTH)
  expect(content().className).not.toContain('max-w-3xl')
  u1()
  const { unmount: u2 } = render(
    <LanguageProvider>
      <ActivityDialog turn={newTurn('q')} open onOpenChange={() => {}} />
    </LanguageProvider>,
  )
  expect(content().className).toContain(WIDTH)
  expect(content().className).not.toContain('max-w-md')
  u2()
  render(
    <LanguageProvider>
      <UsageDialog usage={null} open onOpenChange={() => {}} />
    </LanguageProvider>,
  )
  expect(content().className).toContain(WIDTH)
  expect(content().className).not.toContain('max-w-md')
})

test('a confirmation dialog stays small', () => {
  render(
    <AlertDialog open>
      <AlertDialogContent>
        <AlertDialogTitle>Delete?</AlertDialogTitle>
      </AlertDialogContent>
    </AlertDialog>,
  )
  expect(screen.getByRole('alertdialog').className).not.toContain(WIDTH)
})
