import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api, postJson, setUnauthorizedHandler } from './api'

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  setUnauthorizedHandler(() => {})
})

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status })
}

describe('api', () => {
  it('prefixes /api and returns JSON', async () => {
    fetchMock.mockResolvedValue(json({ name: 'Ann' }))
    await expect(api('/me')).resolves.toEqual({ name: 'Ann' })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/me',
      expect.objectContaining({ credentials: 'same-origin' }),
    )
  })

  it('401 calls the handler and throws', async () => {
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    fetchMock.mockResolvedValue(json({ detail: 'no' }, 401))
    const err = await api('/me').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(401)
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('non-OK throws with detail', async () => {
    fetchMock.mockResolvedValue(json({ detail: 'taken' }, 409))
    const err = await api('/signup').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(409)
    expect((err as ApiError).message).toBe('taken')
    expect((err as ApiError).detail).toBe('taken')
  })

  it('non-JSON error body still throws ApiError', async () => {
    fetchMock.mockResolvedValue(new Response('boom', { status: 500 }))
    const err = await api('/x').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(500)
    expect((err as ApiError).detail).toBeUndefined()
  })

  it('204 returns null', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await expect(api('/logout', { method: 'POST' })).resolves.toBeNull()
  })
})

describe('postJson', () => {
  it('sends JSON and does not call the 401 handler', async () => {
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    fetchMock.mockResolvedValue(json({ detail: 'bad' }, 401))
    const res = await postJson('/login', { username: 'a', password: 'b' })
    expect(res.status).toBe(401)
    expect(handler).not.toHaveBeenCalled()
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/login')
    expect(init.method).toBe('POST')
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json')
    expect(init.body).toBe(JSON.stringify({ username: 'a', password: 'b' }))
  })
})
