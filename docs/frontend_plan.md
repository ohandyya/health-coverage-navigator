# Frontend Plan

Design decisions and implementation plan for the Health Coverage Navigator web UI.

**Status:** design only. Nothing here is implemented yet. This document exists so the API
contract can be locked in now (during Phase 0) while the UI itself gets built alongside
[Phase 1a](plan.md#phase-1a--rag-without-a-vector-database-full-text-search).

**Audience note:** written for someone who is comfortable in Python and new to frontend work.
Where a choice has a "why" that isn't obvious to a backend developer, it's spelled out.

---

## 1. Decisions

| Decision | Choice | Why |
|---|---|---|
| **Stack** | React + Vite + TypeScript + Tailwind CSS | The UI this project needs — streamed text, expandable per-claim citations, a collapsible agent-step tree, Phase 5 comparison tables — is genuinely stateful. A component model pays for itself. It's also the most AI-assistable stack, which matters given the author isn't a frontend specialist. |
| **Backend** | FastAPI, in the existing `health_coverage_navigator` package | Already decided. Serves JSON + SSE; also serves the built frontend in production mode. |
| **Timing** | Contract now, UI with Phase 1a | Define the request/response models and a stub endpoint during Phase 0 so the shape is settled. Real UI work starts when there's an agent to talk to. |
| **Audience** | Localhost, single user | No auth, no accounts, no hosting, no CORS in production. Bind to `127.0.0.1`. |
| **Streaming** | Server-Sent Events (SSE) over a POST + `fetch` reader | PydanticAI streams natively; the traffic is one-directional (server → browser), so WebSockets are unnecessary complexity. |
| **In scope** | Chat with streamed answers, citations + source-type badges, agent-step trace viewer, eval dashboard | All four. See §5. |

Decisions made without asking, because they're conventional defaults:

| Decision | Choice | Why |
|---|---|---|
| Component library | [shadcn/ui](https://ui.shadcn.com) | Copy-paste components into your repo rather than a dependency you're locked into. Accessible defaults for the fiddly bits (dialogs, tooltips, collapsibles) that are easy to get wrong. |
| State management | None — React `useState`/`useReducer` + custom hooks | A two-page app does not need Redux/Zustand/Jotai. Adding one is the most common way a small frontend gets complicated. |
| Data fetching | Plain `fetch` wrapped in hooks | Add TanStack Query only if the eval dashboard's polling becomes annoying. Not before. |
| Routing | React Router, two routes: `/` (chat) and `/evals` | Anything more is premature. |
| Package manager | `npm` | Boring, bundled with Node, one less thing to install. |
| Type safety across the boundary | `openapi-typescript` generating TS types from FastAPI's `/openapi.json` | This is the single highest-leverage choice in this document. See §4.3. |
| Frontend tests | Vitest for the SSE parser and message-reducer logic only | Component tests for a personal tool are low value. The stream parser is not — it's the one place a subtle bug hides. |

---

## 2. Architecture

Two servers in development, one process in production.

```
DEVELOPMENT                                  PRODUCTION (still localhost)
───────────                                  ────────────────────────────
browser :5173                                browser :8000
   │                                            │
   ▼                                            ▼
Vite dev server ──proxy /api──▶ uvicorn      uvicorn :8000
(hot reload)                    :8000           ├── /api/*   → FastAPI routes
                                                └── /*       → StaticFiles(frontend/dist)
```

**Development.** `vite dev` serves the React app on `:5173` with hot-module reload (edit a
component, the browser updates without losing state). Its `server.proxy` config forwards
anything starting with `/api` to `uvicorn` on `:8000`. Because the browser only ever talks to
`:5173`, it's a single origin from the browser's perspective — **no CORS configuration needed,
even in dev.** This is why the proxy is worth setting up rather than enabling permissive CORS.

**Production.** `npm run build` compiles to static files in `frontend/dist/`. FastAPI mounts
that directory and serves it. One process, one port, one command. Nothing about this changes if
you later deploy it — which keeps that door open at near-zero cost.

**Why not two permanently-separate services?** For a localhost single-user tool it buys nothing
and costs a CORS config, a second process to start, and a second thing to deploy.

---

## 3. Repository layout

```
health_coverage_navigator/
├── src/health_coverage_navigator/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── app.py            # FastAPI app factory, static mount, lifespan
│   │   ├── models.py         # ⭐ Pydantic request/response/event models — the contract
│   │   ├── routes/
│   │   │   ├── chat.py       # POST /api/chat, POST /api/chat/stream
│   │   │   ├── evals.py      # eval set + run endpoints
│   │   │   └── corpus.py     # (optional) document lookup for citation drill-down
│   │   └── stub.py           # canned responses so the UI can be built before Phase 1a
│   └── ...
├── frontend/
│   ├── package.json
│   ├── vite.config.ts        # includes the /api proxy
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx           # router
│       ├── api/
│       │   ├── schema.d.ts   # GENERATED from OpenAPI — never hand-edit
│       │   ├── client.ts     # typed fetch wrappers
│       │   └── stream.ts     # SSE parsing (the one unit-tested module)
│       ├── hooks/useChat.ts  # conversation state machine
│       ├── routes/
│       │   ├── ChatPage.tsx
│       │   └── EvalsPage.tsx
│       └── components/
│           ├── MessageList.tsx
│           ├── AnswerBody.tsx      # renders text + inline citation markers
│           ├── CitationCard.tsx    # expandable retrieved chunk
│           ├── SourceBadge.tsx     # reference / api / web
│           ├── AbstentionNotice.tsx
│           └── TracePanel.tsx      # collapsible agent steps
└── docs/frontend_plan.md
```

Add to `.gitignore`:

```
# Frontend
frontend/node_modules/
frontend/dist/
frontend/.vite/
```

`frontend/src/api/schema.d.ts` **is** committed — it's generated, but committing it means the
repo type-checks without running the backend first.

---

## 4. The API contract

This is the Phase 0 deliverable. Everything else can move; this should not, casually.

### 4.1 Design principle

The response shape is dictated by the plan's hard requirement: *every claim carries a
source-type label and the retrieval or URL behind it*
([plan.md](plan.md#cross-cutting-principles-apply-from-day-one)). So the answer is **not** a
string. It's structured from day one, even in Phase 1a where there's only one lane.

Getting this wrong in Phase 1 means reworking the UI at Phase 2, Phase 3, and Phase 4. Getting
it right means those phases only add *values* to existing enums.

### 4.2 Models (illustrative — final form lands in `api/models.py`)

```python
SourceType = Literal["reference", "structured_api", "web"]

class Citation(BaseModel):
    id: str                       # "c1" — referenced from answer text as [c1]
    source_type: SourceType
    title: str
    url: str | None = None        # canonical public URL, when there is one
    doc_id: str | None = None     # corpus id, for reference lane
    snippet: str                  # the retrieved text actually used
    score: float | None = None    # retrieval score, for debugging

class AnswerClaim(BaseModel):
    """A span of the answer plus what backs it. Phase 4 populates this properly;
    Phase 1a may emit a single claim covering the whole answer."""
    text: str
    citation_ids: list[str]

class TraceStep(BaseModel):
    index: int
    kind: Literal["plan", "tool_call", "tool_result", "synthesis"]
    tool: str | None = None
    input: dict[str, Any] | None = None
    summary: str                  # human-readable one-liner for the UI
    duration_ms: int | None = None
    tokens: int | None = None

class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    abstained: bool               # ⭐ first-class, not inferred from the text
    answer: str                   # markdown, with [c1]-style inline markers
    claims: list[AnswerClaim]
    citations: list[Citation]
    trace: list[TraceStep]
    usage: Usage | None = None

class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None   # omit to start a new conversation
    plan_year: int | None = None         # see plan.md: "pin the plan year"
```

Two things worth calling out:

- **`abstained` is a boolean field, not a phrase in the text.** The grounding guardrail says the
  agent must say *"not in my reference material"* rather than hallucinate. The UI must render
  that as a visually distinct state — not styled like a confident answer. If the frontend has to
  regex the answer text to detect abstention, that guardrail is one prompt tweak away from
  breaking silently.
- **`plan_year`** is in the request from day one, per the plan's cross-cutting principle. CMS
  keeps multiple years live simultaneously and mixing them is the domain's most common
  correctness bug.

### 4.3 Type generation (do this — it's the thing that makes a Python dev productive here)

FastAPI already publishes an OpenAPI schema describing every model above. `openapi-typescript`
turns that into TypeScript types:

```make
types: ## Regenerate frontend TS types from the FastAPI OpenAPI schema
	uv run python -m health_coverage_navigator.api.dump_openapi > /tmp/openapi.json
	cd frontend && npx openapi-typescript /tmp/openapi.json -o src/api/schema.d.ts
```

The payoff: change a Pydantic model, run `make types`, and TypeScript immediately errors on
every component that no longer matches. You get backend/frontend contract enforcement for free
instead of discovering drift at runtime. Run it from a static dump rather than a live server so
it works without `uvicorn` running.

### 4.4 Endpoints

| Method | Path | Purpose | Phase |
|---|---|---|---|
| `POST` | `/api/chat` | Non-streaming ask. Returns `ChatResponse`. | 1a (stub in 0) |
| `POST` | `/api/chat/stream` | Same input, `text/event-stream` response. | 1a |
| `GET` | `/api/corpus/{doc_id}` | Full document behind a citation. | 1a |
| `GET` | `/api/evals/questions` | The gold eval set. | 0 |
| `POST` | `/api/evals/runs` | Kick off an eval run (background task). | 0/1a |
| `GET` | `/api/evals/runs` | List past runs with headline metrics. | 0/1a |
| `GET` | `/api/evals/runs/{id}` | Per-question results for one run. | 0/1a |
| `GET` | `/api/health` | Liveness + which lanes are configured. | 0 |

### 4.5 SSE event schema

One stream feeds both the answer pane and the trace panel. Typed events keep that clean:

```
event: start      data: {"conversation_id": "...", "message_id": "..."}
event: step       data: {TraceStep}          ← trace panel appends
event: token      data: {"delta": "Your "}   ← answer pane appends
event: citation   data: {Citation}           ← citation list grows as sources are used
event: done       data: {ChatResponse}       ← authoritative final object
event: error      data: {"message": "..."}
```

The `done` event carries the complete `ChatResponse`. The client replaces its incrementally-
built state with it, so a dropped or malformed `token` event can't leave the UI showing
something subtly wrong — a real concern for a health tool.

**Gotcha (this will bite):** the browser's built-in `EventSource` API only does `GET` and can't
send a request body or custom headers. Since the question goes in a POST body, use `fetch()` and
read `response.body.getReader()`, decoding SSE frames manually. It's roughly 40 lines and lives
in `frontend/src/api/stream.ts`. That module is the one thing worth unit-testing — feed it
chunk boundaries that split mid-frame and mid-UTF-8-character.

**Second gotcha:** SSE event payloads don't automatically appear in the OpenAPI schema, since
FastAPI only sees `StreamingResponse`. To keep §4.3's type generation covering them, define the
event models as a discriminated union and reference it from a route's `responses=` metadata (or
a small schema-only endpoint) so it lands in `openapi.json`.

---

## 5. UI surfaces

### 5.1 Chat page (`/`)

```
┌──────────────────────────────────────────────┬─────────────────────┐
│  Health Coverage Navigator      [Chat][Evals]│  Agent trace     [×]│
├──────────────────────────────────────────────┤                     │
│                                              │ 1 ▸ plan            │
│  You                                         │   decompose question│
│  What is a deductible?                       │                     │
│                                              │ 2 ▾ retrieve        │
│  Navigator                     [reference]   │   q: "deductible"   │
│  A deductible is the amount you pay for      │   8 hits · 240ms    │
│  covered services before your plan begins    │                     │
│  to pay. [c1] After you meet it, you         │ 3 ▸ synthesis       │
│  typically pay only a copayment. [c2]        │   1.2s · 890 tok    │
│                                              │                     │
│  Sources ────────────────────────────────    │                     │
│  ▸ [c1] reference · HealthCare.gov Glossary  │                     │
│  ▸ [c2] reference · Medicare & You 2026 p.41 │                     │
│                                              │                     │
├──────────────────────────────────────────────┤                     │
│  Ask about coverage…            Year: 2026 ▾ │                     │
└──────────────────────────────────────────────┴─────────────────────┘
```

Requirements:

- **Source badges** are color-coded by lane and consistent everywhere: `reference` (blue),
  `structured_api` (green), `web` (amber). In Phase 1a everything is blue — that's fine, the
  vocabulary is already there for Phase 2 and 3.
- **Citation cards** collapse to a one-line source label and expand to the `snippet` actually
  retrieved, with a link to the full document (`/api/corpus/{doc_id}`) or external `url`.
- **Inline markers** (`[c1]`) in the answer scroll to and highlight the matching citation. Keep
  markdown rendering restricted — headings, lists, bold, links, tables. No raw HTML.
- **Abstention** renders as a distinct muted panel with its own icon and no citation section.
  It must be impossible to mistake for an answer at a glance.
- **Plan-year selector** sits next to the input and is sent on every request.
- **Trace panel** is collapsible, closed by default, and remembers its state in `localStorage`.
  It is a developer surface, not a user feature — but it's *your* primary debugging tool, so it
  should be good.

### 5.2 Eval dashboard (`/evals`)

The goal: turn eval output from a terminal dump you skim into something you actually look at
every iteration.

```
┌──────────────────────────────────────────────────────────────────┐
│  Eval runs                                    [ Run gold set ▶ ] │
├──────────────────────────────────────────────────────────────────┤
│  run_2026-07-31_1  full-text  recall@5 0.72  MRR 0.61  24/30  ▸  │
│  run_2026-07-30_2  full-text  recall@5 0.68  MRR 0.55  22/30  ▸  │
├──────────────────────────────────────────────────────────────────┤
│  Expanded: run_2026-07-31_1                                      │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ ✓ What is a deductible?          reference  rank 1         │  │
│  │ ✗ Does Medicare cover hearing…   reference  not retrieved  │  │
│  │ ✓ 2026 open enrollment dates     web        rank 2         │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

- Metric columns grow with the phases: retrieval (recall@k, MRR) at Phase 0/1a, answer
  correctness + groundedness at Phase 1, routing accuracy at Phase 2/3, multi-hop and citation
  accuracy at Phase 4. The table renders whatever metrics a run reports rather than hardcoding a
  fixed set.
- Clicking a failed question opens the retrieved chunks against the expected answer — the fastest
  path from "recall dropped" to "here's why."
- **Run comparison** (pick two runs, diff per-question outcomes) is what makes the Phase 1b
  vector-vs-lexical decision concrete. Worth building when Phase 1b starts, not before.
- Runs persist as JSON under `data/eval_runs/`. This directory is generated output — add it to
  `.gitignore` unless you deliberately want run history committed.

---

## 6. Build phases

The frontend tracks the backend phases rather than running ahead of them.

**Phase F0 — Contract + skeleton (now, alongside Phase 0)**
- [ ] `api/models.py` with the §4.2 models
- [ ] `api/app.py`, `/api/health`, and a stubbed `POST /api/chat` returning a canned
      `ChatResponse` with two fake citations and a fake trace
- [ ] `frontend/` scaffolded: Vite + React + TS + Tailwind + shadcn, `/api` proxy configured
- [ ] `make types` wired up and producing `schema.d.ts`
- [ ] Chat page rendering the stub end to end: message, badges, citation cards, trace panel
- [ ] Eval endpoints over the gold set once it exists

*Done when:* `make dev` opens a browser, you type a question, and a fully-rendered fake answer
comes back. No agent involved. The entire UI is proven before there's anything real behind it.

**Phase F1 — Real answers (with Phase 1a)**
- [ ] Swap the stub for the PydanticAI agent
- [ ] `POST /api/chat/stream` + `stream.ts` + its unit tests
- [ ] Abstention state wired to the real guardrail
- [ ] Citation drill-down against the real corpus
- [ ] Eval dashboard against real runs

**Phase F2 — Routing visible (with Phase 2/3)**
- [ ] `structured_api` and `web` badges become live
- [ ] Routing-accuracy column in the eval dashboard
- [ ] Per-lane filtering of citations

**Phase F3 — Multi-step (with Phase 4)**
- [ ] Trace panel handles nested/multi-hop steps
- [ ] Per-claim provenance: hovering a claim highlights exactly its citations
- [ ] Latency and token cost surfaced per run

**Phase F4 — Product surfaces (with Phase 5)**
- [ ] Plan comparison tables, drug-cost breakdowns
- [ ] Monitor / "what changed" view

---

## 7. Developer workflow

New Makefile targets:

```make
ui-install:   ## Install frontend dependencies
	cd frontend && npm install

ui-dev:       ## Vite dev server with hot reload (:5173)
	cd frontend && npm run dev

api-dev:      ## FastAPI with autoreload (:8000)
	uv run uvicorn health_coverage_navigator.api.app:app --reload --host 127.0.0.1

dev:          ## Both, side by side
	$(MAKE) -j2 api-dev ui-dev

ui-build:     ## Compile the frontend into frontend/dist
	cd frontend && npm run build

types:        ## Regenerate TS types from the OpenAPI schema
	...

serve:        ## Single-process mode: FastAPI serving the built UI (:8000)
	$(MAKE) ui-build && uv run uvicorn health_coverage_navigator.api.app:app --host 127.0.0.1
```

Also: extend `check-all` to run `tsc --noEmit` and `npm run lint` (ESLint) so the frontend is
covered by the same gate as `ruff` and `pyright`. Add the frontend lint step to
`.pre-commit-config.yaml` only if it's fast enough not to be annoying — otherwise leave it in
`check-all`.

New dependencies: `fastapi`, `uvicorn[standard]`, `sse-starlette` (optional, tidier SSE than
hand-rolling), `pydantic` (already transitive via PydanticAI later).

---

## 8. Constraints and guardrails

- **Bind to `127.0.0.1`, never `0.0.0.0`.** With no auth, `0.0.0.0` exposes the app to your
  whole network.
- **Secrets stay server-side.** The CMS Marketplace API key and any LLM key live in `.env` (already
  gitignored) and are read by FastAPI. They must never appear in a frontend file, a Vite
  `VITE_*` env var, or an API response — anything in the built bundle is public by definition.
- **No arbitrary-path file serving.** `/api/corpus/{doc_id}` must resolve IDs through the corpus
  index, not concatenate user input onto a filesystem path. Localhost doesn't make path
  traversal acceptable.
- **The public-repo data guardrail still applies.** Nothing the UI generates gets committed under
  `data/` without clearing [CLAUDE.md](../CLAUDE.md#public-repo-data-guardrail-action-required-before-committing-data).
  Eval run outputs are generated artifacts — gitignore them by default.
- **Don't render model output as raw HTML.** Markdown only, with HTML disabled in the renderer.
- **Answers carry no medical or enrollment advice framing.** The UI presents cited reference
  material; a persistent, unobtrusive disclaimer belongs in the footer, not a modal.

---

## 9. Explicitly out of scope

Listed so they're deliberate omissions, not oversights: authentication and user accounts; mobile
layouts (responsive-enough is enough, this is a desktop tool); dark mode (nice, not now);
conversation persistence across restarts — conversations live in an in-memory dict keyed by
`conversation_id`, and the contract already carries the ID, so adding SQLite later changes only
the storage layer; internationalization; PWA/offline; and any deployment target.

---

## 10. Open questions

1. **Multi-turn conversation.** The contract assumes conversations are multi-turn with
   server-side in-memory history. If the agent is single-shot in Phase 1a, `conversation_id` is
   carried but unused — harmless, and it avoids a contract change later. Confirm that's the
   intent.
2. **Eval runs from the browser.** Triggering a run over HTTP means a long-running background
   task and a progress channel (probably SSE again, reusing §4.5's machinery). Alternative: the
   dashboard is read-only over runs produced by a CLI. Read-only is meaningfully simpler; decide
   at F0.
3. **Corpus browser.** A "search the 886 ingested documents" page was considered and left out of
   v1. Citation drill-down covers most of the need. Revisit if inspecting the corpus by hand
   turns out to be a frequent debugging move.
