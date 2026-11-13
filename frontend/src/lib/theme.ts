// Chatroom is always dark and Light always light; every other theme follows the system.
const FORCED: Record<string, boolean> = { chatroom: true, light: false }

/** Sets or clears `.dark` for the current `data-theme`; `system` is whether the system prefers dark. */
export function applyDark(system: boolean): void {
  const dark = FORCED[document.documentElement.dataset.theme ?? ''] ?? system
  document.documentElement.classList.toggle('dark', dark)
}

export function systemDark(): boolean {
  return window.matchMedia?.('(prefers-color-scheme: dark)')?.matches ?? false
}

export function initTheme(): () => void {
  const query = window.matchMedia('(prefers-color-scheme: dark)')
  const onChange = (e: { matches: boolean }) => applyDark(e.matches)
  applyDark(query.matches)
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}
