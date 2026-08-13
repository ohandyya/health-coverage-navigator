/**
 * Typed wrappers over the API.
 *
 * Every type below is an *alias into* `schema.d.ts`, never a hand-written interface. That is the
 * rule CLAUDE.md states and the payoff docs/frontend_plan.md §4.3 promises: change a Pydantic
 * model, run `make types`, and TypeScript immediately errors in every component that no longer
 * matches. A duplicated interface would quietly keep compiling while the contract drifted.
 */
import type { components } from './schema'

export type ChatRequest = components['schemas']['ChatRequest']
export type ChatResponse = components['schemas']['ChatResponse']
export type Citation = components['schemas']['Citation']
export type AnswerClaim = components['schemas']['AnswerClaim']
export type TraceStep = components['schemas']['TraceStep']
export type HealthResponse = components['schemas']['HealthResponse']
export type CorpusDocument = components['schemas']['CorpusDocument']
export type GoldQuestion = components['schemas']['GoldQuestion']
export type EvalQuestionsResponse = components['schemas']['EvalQuestionsResponse']
export type EvalRun = components['schemas']['EvalRun']
export type EvalRunSummary = components['schemas']['EvalRunSummary']
export type EvalRunStarted = components['schemas']['EvalRunStarted']

/** The lane vocabulary, derived rather than restated — adding a lane in Python breaks this here. */
export type SourceType = Citation['source_type']

/** One SSE frame from `POST /api/chat/stream`, discriminated on `type`. */
export type StreamEvent = components['schemas']['StreamEventEnvelope']['event']

/** One SSE frame from `GET /api/evals/runs/{id}/stream`. */
export type EvalRunEvent = components['schemas']['EvalRunEventEnvelope']['event']

export class ApiError extends Error {
  // Declared and assigned separately rather than as a parameter property: the tsconfig sets
  // `erasableSyntaxOnly`, which rejects TypeScript syntax that a plain type-strip cannot remove.
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(path, init)
  if (!response.ok) {
    // FastAPI puts the useful part in `detail`; fall back to the status text when the body is
    // not JSON (a proxy error page, say).
    let detail = response.statusText
    try {
      detail = ((await response.json()) as { detail?: string }).detail ?? detail
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, detail)
  }
  return response
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  return (await request(path, init)).json() as Promise<T>
}

const POST_JSON = {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
}

export function getHealth(): Promise<HealthResponse> {
  return json('/api/health')
}

export function postChat(body: ChatRequest): Promise<ChatResponse> {
  return json('/api/chat', { ...POST_JSON, body: JSON.stringify(body) })
}

/** The raw streaming response — `stream.ts` turns it into typed events. */
export function postChatStream(body: ChatRequest, signal?: AbortSignal): Promise<Response> {
  return request('/api/chat/stream', { ...POST_JSON, body: JSON.stringify(body), signal })
}

export function getDocument(docId: string): Promise<CorpusDocument> {
  return json(`/api/corpus/${encodeURIComponent(docId)}`)
}

export function getEvalQuestions(): Promise<EvalQuestionsResponse> {
  return json('/api/evals/questions')
}

export function getEvalRuns(): Promise<EvalRunSummary[]> {
  return json('/api/evals/runs')
}

export function getEvalRun(runId: string): Promise<EvalRun> {
  return json(`/api/evals/runs/${encodeURIComponent(runId)}`)
}

export function startEvalRun(): Promise<EvalRunStarted> {
  return json('/api/evals/runs', { method: 'POST' })
}

export function streamEvalRun(runId: string, signal?: AbortSignal): Promise<Response> {
  return request(`/api/evals/runs/${encodeURIComponent(runId)}/stream`, { signal })
}
