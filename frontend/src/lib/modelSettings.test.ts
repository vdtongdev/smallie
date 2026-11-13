import { beforeEach, describe, expect, it } from 'vitest'
import { cachedModels, modelOptions, PROFILES, storeModels, unchanged } from './modelSettings'

beforeEach(() => localStorage.clear())

describe('profiles', () => {
  it('are economy, balanced, advanced', () => {
    expect([...PROFILES]).toEqual(['economy', 'balanced', 'advanced'])
  })
})

describe('model cache', () => {
  it('is empty when nothing is stored', () => {
    expect(cachedModels('https://a/v1')).toEqual([])
  })
  it('returns the stored list sorted', () => {
    localStorage.setItem('models:https://a/v1', JSON.stringify({ models: ['b', 'c', 'a'], fetched_at: 1 }))
    expect(cachedModels('https://a/v1')).toEqual(['a', 'b', 'c'])
  })
  it('treats bad JSON or a non-list as empty', () => {
    localStorage.setItem('models:https://a/v1', '{nope')
    expect(cachedModels('https://a/v1')).toEqual([])
    localStorage.setItem('models:https://a/v1', JSON.stringify({ models: 'x' }))
    expect(cachedModels('https://a/v1')).toEqual([])
  })
  it('keeps one list per endpoint', () => {
    storeModels('https://a/v1', ['x'])
    expect(cachedModels('https://a/v1')).toEqual(['x'])
    expect(cachedModels('https://b/v1')).toEqual([])
  })
  it('stores {models, fetched_at} under models:<base_url>', () => {
    storeModels('https://a/v1', ['x', 'y'])
    const raw = JSON.parse(localStorage.getItem('models:https://a/v1')!)
    expect(raw.models).toEqual(['x', 'y'])
    expect(typeof raw.fetched_at).toBe('number')
  })
})

describe('modelOptions', () => {
  const ids = (o: { value: string }[]) => o.map((x) => x.value)
  it('lists the models', () => {
    expect(modelOptions(['a', 'b'], 'a')).toEqual([
      { value: 'a', label: 'a' },
      { value: 'b', label: 'b' },
    ])
  })
  it('puts a blank option first when given a blank label', () => {
    expect(modelOptions(['a'], '', 'Same')).toEqual([
      { value: '', label: 'Same' },
      { value: 'a', label: 'a' },
    ])
  })
  it('adds a saved model the provider does not list, labelled not listed', () => {
    const opts = modelOptions(['a'], 'old', undefined, (id) => `${id} (gone)`)
    expect(ids(opts)).toEqual(['a', 'old'])
    expect(opts[1].label).toBe('old (gone)')
  })
  it('shows the saved model plainly when there is no list at all', () => {
    expect(modelOptions([], 'old', 'Same', (id) => `${id} (gone)`)).toEqual([
      { value: '', label: 'Same' },
      { value: 'old', label: 'old' },
    ])
  })
  it('adds nothing for a blank value or a listed one', () => {
    expect(ids(modelOptions(['a'], ''))).toEqual(['a'])
    expect(ids(modelOptions(['a'], 'a'))).toEqual(['a'])
  })
})

describe('unchanged', () => {
  const loaded = { base_url: 'u', model: 'm', small_model: 's', strong_model: 'g', profile: 'balanced' }
  it('is true when every field is equal', () => {
    expect(unchanged({ ...loaded }, loaded)).toBe(true)
  })
  it.each(['base_url', 'model', 'small_model', 'strong_model', 'profile'])('is false when %s differs', (k) => {
    expect(unchanged({ ...loaded, [k]: 'other' }, loaded)).toBe(false)
  })
})
