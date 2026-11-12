import { vi } from 'vitest'

export const me = { user_id: 'u1', name: 'An', username: 'an', is_admin: false, max_accounts: 5 }

const enc = new TextEncoder()

/** An SSE response body (`data: {json}\n\n` per event). `failAfter` makes the reader throw once the events are read. */
export function sseResponse(events: object[], failAfter?: Error): Response {
  let sent = false
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (!sent) {
        sent = true
        controller.enqueue(enc.encode(events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')))
        if (!failAfter) controller.close()
      } else if (failAfter) controller.error(failAfter)
    },
  })
  return new Response(body, { status: 200 })
}

export const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })

type Handler = (init?: RequestInit) => Response | Promise<Response>
export type Call = { method: string; url: string; init?: RequestInit }

/** Stubs `fetch`. `routes` is keyed `"METHOD /api/path"`; unlisted routes give the empty answers below. */
export function installFetch(routes: Record<string, Handler> = {}) {
  const calls: Call[] = []
  const defaults: Record<string, Handler> = {
    'GET /api/me': () => json(me),
    'GET /api/agents': () => json([]),
    'GET /api/conversations': () => json([]),
    'GET /api/documents': () => json([]),
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, url, init })
      const handler = routes[`${method} ${url}`] ?? defaults[`${method} ${url}`]
      return handler ? handler(init) : json({ detail: 'not found' }, 404)
    }),
  )
  return calls
}
