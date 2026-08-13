/**
 * The SSE parser is the only frontend module worth unit-testing at Phase 0
 * (docs/frontend_plan.md §1). Everything here feeds it a byte boundary chosen to break it.
 */
import { describe, expect, it } from 'vitest'
import { readEvents, readSSE } from './stream'

/** A stream that hands out exactly these byte chunks, so boundaries are under test control. */
function streamOf(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk)
      controller.close()
    },
  })
}

function bytes(text: string): Uint8Array {
  return new TextEncoder().encode(text)
}

/** Split encoded bytes at an absolute byte offset — the point is to split mid-character. */
function splitAt(text: string, offset: number): Uint8Array[] {
  const encoded = bytes(text)
  return [encoded.slice(0, offset), encoded.slice(offset)]
}

async function collect<T>(gen: AsyncGenerator<T>): Promise<T[]> {
  const out: T[] = []
  for await (const item of gen) out.push(item)
  return out
}

const FRAMES =
  'event: start\ndata: {"type":"start","message_id":"m1"}\n\n' +
  'event: token\ndata: {"type":"token","delta":"Hello "}\n\n' +
  'event: done\ndata: {"type":"done"}\n\n'

describe('readSSE', () => {
  it('parses several frames delivered in one chunk', async () => {
    const frames = await collect(readSSE(streamOf([bytes(FRAMES)])))
    expect(frames.map((f) => f.event)).toEqual(['start', 'token', 'done'])
  })

  it('parses a frame split across two chunks', async () => {
    // Cut in the middle of the second frame's JSON payload.
    const cut = FRAMES.indexOf('"delta"')
    const chunks = [bytes(FRAMES.slice(0, cut)), bytes(FRAMES.slice(cut))]
    const frames = await collect(readSSE(streamOf(chunks)))
    expect(frames.map((f) => f.event)).toEqual(['start', 'token', 'done'])
    expect(JSON.parse(frames[1].data)).toEqual({ type: 'token', delta: 'Hello ' })
  })

  it('parses a frame split inside its terminating blank line', async () => {
    const cut = FRAMES.indexOf('\n\n') + 1
    const frames = await collect(readSSE(streamOf([bytes(FRAMES.slice(0, cut)), bytes(FRAMES.slice(cut))])))
    expect(frames).toHaveLength(3)
  })

  it('does not corrupt a multi-byte character split across chunks', async () => {
    // The corpus is full of smart quotes and en dashes; “doctors’ services” below is real text
    // from Medicare & You. Splitting mid-code-point is what `TextDecoder({stream:true})` fixes.
    const text = 'event: token\ndata: {"type":"token","delta":"doctors’ services — 20%"}\n\n'
    const dashOffset = bytes(text).indexOf(0xe2) // first byte of a 3-byte UTF-8 sequence
    const frames = await collect(readSSE(streamOf(splitAt(text, dashOffset + 1))))
    expect(JSON.parse(frames[0].data).delta).toBe('doctors’ services — 20%')
  })

  it('emits a trailing frame that arrives without a blank line', async () => {
    const frames = await collect(readSSE(streamOf([bytes('event: done\ndata: {"type":"done"}')])))
    expect(frames).toHaveLength(1)
    expect(frames[0].event).toBe('done')
  })

  it('ignores comments and keep-alive lines', async () => {
    const text = ': keep-alive\n\nevent: token\ndata: {"type":"token"}\n\n'
    const frames = await collect(readSSE(streamOf([bytes(text)])))
    expect(frames.map((f) => f.event)).toEqual(['token'])
  })

  it('joins multi-line data fields', async () => {
    const frames = await collect(readSSE(streamOf([bytes('event: x\ndata: a\ndata: b\n\n')])))
    expect(frames[0].data).toBe('a\nb')
  })

  it('handles CRLF framing', async () => {
    const text = 'event: token\r\ndata: {"type":"token"}\r\n\r\n'
    const frames = await collect(readSSE(streamOf([bytes(text)])))
    expect(frames.map((f) => f.event)).toEqual(['token'])
  })
})

describe('readEvents', () => {
  it('yields typed events discriminated by the payload, not the frame header', async () => {
    const response = new Response(streamOf([bytes(FRAMES)]))
    const events = await collect(readEvents<{ type: string }>(response))
    expect(events.map((e) => e.type)).toEqual(['start', 'token', 'done'])
  })

  it('skips a malformed payload rather than aborting the stream', async () => {
    const text = 'event: token\ndata: {not json}\n\nevent: done\ndata: {"type":"done"}\n\n'
    const events = await collect(readEvents<{ type: string }>(new Response(streamOf([bytes(text)]))))
    expect(events.map((e) => e.type)).toEqual(['done'])
  })

  it('throws when the response has no body', async () => {
    await expect(collect(readEvents(new Response(null)))).rejects.toThrow('no body')
  })
})
