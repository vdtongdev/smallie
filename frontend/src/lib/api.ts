export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, detail: unknown, statusText: string) {
    super(typeof detail === 'string' ? detail : statusText)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

let onUnauthorized: () => void = () => {}

export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, { credentials: 'same-origin', ...init })
  if (res.status === 401) {
    onUnauthorized()
    throw new ApiError(401, undefined, res.statusText)
  }
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown }
    throw new ApiError(res.status, body.detail, res.statusText)
  }
  return (res.status === 204 ? null : await res.json()) as T
}

export function postJson(path: string, body: unknown): Promise<Response> {
  return fetch(`/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** For the one call that streams with `fetch` instead of `api`: a 401 there logs out like any other. */
export function unauthorized(): void {
  onUnauthorized()
}
