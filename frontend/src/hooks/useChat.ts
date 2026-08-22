/**
 * Conversation state.
 *
 * A `useReducer` rather than a handful of `useState`s because the streaming path fires four
 * different kinds of update (`start`, `step`, `token`, `citation`) against the same in-flight
 * message, and coordinating those across separate setters is where a partially-rendered answer
 * comes from.
 *
 * The load-bearing rule: on `done`, the incrementally-built message is **replaced** by the
 * `ChatResponse` the server sent, not merged with it. docs/frontend_plan.md §4.5 makes `done`
 * authoritative for exactly this reason — a dropped or malformed `token` frame must not be able to
 * leave the UI showing an answer that is subtly wrong, which in a health-coverage tool is the
 * failure that matters.
 */
import { useCallback, useReducer, useRef } from 'react'
import {
  ApiError,
  type ChatResponse,
  type Citation,
  type StreamEvent,
  type TraceStep,
  postChatStream,
} from '@/api/client'
import { readEvents } from '@/api/stream'

export interface UserMessage {
  role: 'user'
  id: string
  text: string
}

export interface AssistantMessage {
  role: 'assistant'
  id: string
  /** Text accumulated from `token` events — replaced wholesale by `done`, and by any `token`
   *  carrying `reset` (an abandoned draft; see the `token` case in the reducer). */
  answer: string
  abstained: boolean
  citations: Citation[]
  claims: ChatResponse['claims']
  trace: TraceStep[]
  usage: ChatResponse['usage']
  /** True until `done` lands, so the UI can show a caret and disable the composer. */
  streaming: boolean
  error?: string
}

export type Message = UserMessage | AssistantMessage

interface State {
  messages: Message[]
  busy: boolean
}

type Action =
  | { kind: 'ask'; text: string; messageId: string }
  | { kind: 'event'; event: StreamEvent }
  | { kind: 'fail'; message: string }

function emptyAssistant(id: string): AssistantMessage {
  return {
    role: 'assistant',
    id,
    answer: '',
    abstained: false,
    citations: [],
    claims: [],
    trace: [],
    usage: null,
    streaming: true,
  }
}

/** Apply `update` to the last assistant message; every streaming action targets that one. */
function patchLast(state: State, update: (m: AssistantMessage) => AssistantMessage): State {
  const messages = [...state.messages]
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i]
    if (message.role === 'assistant') {
      messages[i] = update(message)
      return { ...state, messages }
    }
  }
  return state
}

export function chatReducer(state: State, action: Action): State {
  switch (action.kind) {
    case 'ask':
      return {
        busy: true,
        messages: [
          ...state.messages,
          { role: 'user', id: `u_${action.messageId}`, text: action.text },
          emptyAssistant(action.messageId),
        ],
      }

    case 'fail':
      return patchLast({ ...state, busy: false }, (m) => ({
        ...m,
        streaming: false,
        error: action.message,
      }))

    case 'event': {
      const event = action.event
      switch (event.type) {
        case 'start':
          return patchLast(state, (m) => ({ ...m, id: event.message_id }))
        case 'step':
          return patchLast(state, (m) => ({ ...m, trace: [...m.trace, event.step] }))
        case 'token':
          // `reset` means the agent abandoned the draft streamed so far — the grounding guardrail
          // rejected it and the retry is writing something different. Appending would leave the
          // rejected draft on screen above the answer that replaced it, and the rejected draft is
          // by construction the ungrounded one. Replace rather than append.
          return patchLast(state, (m) => ({
            ...m,
            answer: event.reset ? event.delta : m.answer + event.delta,
          }))
        case 'citation':
          return patchLast(state, (m) => ({ ...m, citations: [...m.citations, event.citation] }))
        case 'done': {
          // Replace, do not merge. See the module docstring.
          const r = event.response
          return patchLast({ ...state, busy: false }, (m) => ({
            ...m,
            id: r.message_id,
            answer: r.answer,
            abstained: r.abstained,
            citations: r.citations,
            claims: r.claims,
            trace: r.trace,
            usage: r.usage,
            streaming: false,
          }))
        }
        case 'error':
          return patchLast({ ...state, busy: false }, (m) => ({
            ...m,
            streaming: false,
            error: event.message,
          }))
      }
    }
  }
}

export function useChat() {
  const [state, dispatch] = useReducer(chatReducer, { messages: [], busy: false })
  const abort = useRef<AbortController | null>(null)

  const ask = useCallback(async (text: string, planYear: number | null) => {
    const provisionalId = `pending_${Date.now()}`
    dispatch({ kind: 'ask', text, messageId: provisionalId })

    abort.current?.abort()
    const controller = new AbortController()
    abort.current = controller

    try {
      const response = await postChatStream(
        { message: text, plan_year: planYear, conversation_id: null },
        controller.signal,
      )
      for await (const event of readEvents<StreamEvent>(response)) {
        dispatch({ kind: 'event', event })
      }
    } catch (err) {
      if (controller.signal.aborted) return
      const message =
        err instanceof ApiError ? `${err.status}: ${err.message}` : String(err)
      dispatch({ kind: 'fail', message })
    }
  }, [])

  return { messages: state.messages, busy: state.busy, ask }
}
