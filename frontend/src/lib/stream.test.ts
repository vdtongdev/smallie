import { describe, expect, it, vi } from 'vitest'
import { readEvents } from './stream'
import type { ChatEvent } from './types'

function streamOf(chunks: (string | Uint8Array)[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(typeof c === 'string' ? enc.encode(c) : c)
      controller.close()
    },
  })
}

async function collect(body: ReadableStream<Uint8Array>): Promise<ChatEvent[]> {
  const out: ChatEvent[] = []
  for await (const e of readEvents(body)) out.push(e)
  return out
}

describe('readEvents', () => {
  it('joins an event split across two chunks', async () => {
    const events = await collect(streamOf(['data: {"type":"tok', 'en","text":"hi"}\n\n']))
    expect(events).toEqual([{ type: 'token', text: 'hi' }])
  })

  it('yields two events from one chunk', async () => {
    const events = await collect(
      streamOf(['data: {"type":"agent","name":"sql"}\n\ndata: {"type":"done"}\n\n']),
    )
    expect(events).toEqual([{ type: 'agent', name: 'sql' }, { type: 'done' }])
  })

  it('skips malformed JSON and still yields the next event', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const events = await collect(streamOf(['data: {oops\n\ndata: {"type":"done"}\n\n']))
    expect(events).toEqual([{ type: 'done' }])
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  it('ignores non-data lines', async () => {
    const events = await collect(streamOf([': ping\n\nevent: x\n\ndata: {"type":"done"}\n\n']))
    expect(events).toEqual([{ type: 'done' }])
  })

  it('yields nothing for a partial event at the end', async () => {
    const events = await collect(streamOf(['data: {"type":"done"}\n\ndata: {"type":"tok']))
    expect(events).toEqual([{ type: 'done' }])
  })

  it('decodes multi-byte UTF-8 split across chunks', async () => {
    const bytes = new TextEncoder().encode('data: {"type":"token","text":"Xin chào"}\n\n')
    const cut = bytes.indexOf(0xc3) + 1 // inside "à"
    const events = await collect(streamOf([bytes.slice(0, cut), bytes.slice(cut)]))
    expect(events).toEqual([{ type: 'token', text: 'Xin chào' }])
  })
})
