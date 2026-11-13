import { localGet, localSet } from './storage'

export const PROFILES = ['economy', 'balanced', 'advanced'] as const
export type Profile = (typeof PROFILES)[number]

/** What the form saves and what GET /api/model-settings returns (besides `key_hint`). */
export type Slots = { base_url: string; model: string; small_model: string; strong_model: string; profile: string }

// The provider's model list is cached per endpoint; `/` and `/next` share it.
const modelsKey = (baseUrl: string) => `models:${baseUrl}`

export function cachedModels(baseUrl: string): string[] {
  try {
    const list = JSON.parse(localGet(modelsKey(baseUrl)) || 'null')?.models
    return Array.isArray(list) ? [...list].sort() : []
  } catch {
    return []
  }
}

export function storeModels(baseUrl: string, models: string[]): void {
  localSet(modelsKey(baseUrl), JSON.stringify({ models, fetched_at: Date.now() }))
}

export type ModelOption = { value: string; label: string }

/** The select's options. A saved id the provider does not list is kept, labelled `notListed` when there is a list. */
export function modelOptions(
  models: string[],
  value: string,
  blankLabel?: string,
  notListed?: (id: string) => string,
): ModelOption[] {
  const opts: ModelOption[] = blankLabel === undefined ? [] : [{ value: '', label: blankLabel }]
  opts.push(...models.map((id) => ({ value: id, label: id })))
  if (value && !models.includes(value)) {
    opts.push({ value, label: models.length && notListed ? notListed(value) : value })
  }
  return opts
}

export function unchanged(next: Slots, loaded: Slots): boolean {
  return (Object.keys(next) as (keyof Slots)[]).every((k) => next[k] === loaded[k])
}
