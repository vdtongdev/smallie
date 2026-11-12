export function initTheme(): () => void {
  const query = window.matchMedia('(prefers-color-scheme: dark)')
  const apply = (dark: boolean) => document.documentElement.classList.toggle('dark', dark)
  const onChange = (e: { matches: boolean }) => apply(e.matches)
  apply(query.matches)
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}
