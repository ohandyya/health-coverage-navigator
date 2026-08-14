/**
 * Server-Sent Events, read off a `fetch` response body.
 *
 * **Why not `EventSource`.** The browser's built-in SSE client only issues `GET` and cannot send
 * a request body or custom headers (docs/frontend_plan.md §4.5). The question goes in a POST body,
 * so the frames have to be decoded by hand.
 *
 * This is the one frontend module with real logic rather than markup, and the two bugs it can hide
 * are both invisible in normal use:
 *
 *   1. **A frame split across chunk boundaries.** TCP does not respect `\n\n`, so a naive
 *      "parse each chunk" reader drops or corrupts events only under load. Fixed by buffering
 *      until a blank line is actually seen.
 *   2. **A multi-byte character split across chunk boundaries.** `TextDecoder` in `{ stream: true }`
 *      mode holds the partial code point until the rest arrives; decoding each chunk
 *      independently turns "±" into a replacement character. This corpus is full of “smart”
 *      punctuation and en dashes, so it would show up immediately — as mangled text in an answer,
 *      which for a health tool is the wrong kind of wrong.
 *
 * Both are covered in `stream.test.ts`, which is the only frontend test that exists at Phase 0.
 */

/** One decoded SSE frame, before it is interpreted as a typed event. */
export interface SSEFrame {
  event: string
  data: string
}

/**
 * Split an SSE byte stream into frames.
 *
 * Deliberately untyped at this layer: it knows the SSE wire format and nothing about the
 * application's events, which is what makes it reusable for both the chat stream and the eval
 * progress stream.
 */
export async function* readSSE(body: ReadableStream<Uint8Array>): AsyncGenerator<SSEFrame> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      // `stream: true` is what carries a partial multi-byte character across the boundary.
      buffer += decoder.decode(value, { stream: true })

      let split: number
      // Servers may use \n\n or \r\n\r\n; both terminate a frame.
      while ((split = findFrameEnd(buffer)) !== -1) {
        const raw = buffer.slice(0, split)
        buffer = buffer.slice(split).replace(/^(\r?\n){2}/, '')
        const frame = parseFrame(raw)
        if (frame) yield frame
      }
    }

    // Flush whatever the decoder is still holding, then emit a trailing frame that arrived
    // without its terminating blank line — a stream that ends cleanly on its last event.
    buffer += decoder.decode()
    const frame = parseFrame(buffer)
    if (frame) yield frame
  } finally {
    reader.releaseLock()
  }
}

function findFrameEnd(buffer: string): number {
  const lf = buffer.indexOf('\n\n')
  const crlf = buffer.indexOf('\r\n\r\n')
  if (lf === -1) return crlf
  if (crlf === -1) return lf
  return Math.min(lf, crlf)
}

function parseFrame(raw: string): SSEFrame | null {
  let event = 'message'
  const data: string[] = []

  for (const line of raw.split(/\r?\n/)) {
    if (!line || line.startsWith(':')) continue // blank line or comment/keep-alive
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    // A single leading space after the colon is part of the framing, not the value.
    const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '')
    if (field === 'event') event = value
    else if (field === 'data') data.push(value)
  }

  return data.length > 0 ? { event, data: data.join('\n') } : null
}

/**
 * Decode frames into typed events.
 *
 * The discriminant is read from the JSON payload rather than the frame's `event:` line: the
 * server derives that line *from* the payload, so the payload is the source of truth, and it is
 * what makes the generated TypeScript union narrow. A frame whose payload will not parse is
 * skipped rather than fatal — the `done` event is authoritative, so one bad `token` must not take
 * the whole answer down.
 */
export async function* readEvents<T extends { type: string }>(
  response: Response,
): AsyncGenerator<T> {
  if (!response.body) throw new Error('response has no body to stream')
  for await (const frame of readSSE(response.body)) {
    let parsed: unknown
    try {
      parsed = JSON.parse(frame.data)
    } catch {
      continue
    }
    if (parsed && typeof parsed === 'object' && 'type' in parsed) {
      yield parsed as T
    }
  }
}
