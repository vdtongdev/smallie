import type { ChatEvent } from './types'

export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<ChatEvent> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const raws = buffer.split('\n\n')
    buffer = raws.pop() ?? ''
    for (const raw of raws) {
      if (!raw.startsWith('data: ')) continue
      try {
        yield JSON.parse(raw.slice(6)) as ChatEvent
      } catch (err) {
        console.warn('Skipped a bad stream event', raw, err)
      }
    }
  }
}
