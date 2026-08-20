# Progress

Status of the build against [plan.md](plan.md) and [frontend_plan.md](frontend_plan.md).
**Those docs say what to build; this one says what is built.** They never record completion;
this one never records design.

**How to read this:** the *Current state* block below is the one-read answer to "where am I".
The *Log* is append-only, newest first — read down as far as you need for the reasoning behind
the current state. A log entry is true as of its date and is never edited.

**What does not belong here:** anything git history or the code already says. No file
inventories, no restating what a script does. The value of this file is entirely in what cannot
be reconstructed — decisions, rejected alternatives, dead ends, and where work stopped
mid-stream.

---

## Current state

*Updated 2026-08-19.*

- **Phase:** **Phase 2 is complete and measured; the agent has three lanes.**
  `web_search` over Tavily is registered beside the reference and relational tools, cites web
  results by a `result_id` only a search can assign, and degrades to an honest *"I could not check
  the web"* on a rate limit or an outage. `make check-all` is green (373 tests, pyright clean) and
  `make smoke-web` passes **12/12 against live Tavily**; a reference question still routes to
  `search_corpus` alone with the web tool registered (`make smoke`, 9/9), so no over-reaching on the
  one live sample there is. Design: [web_search_tool.md](web_search_tool.md); §17 there records what
  building it changed about the design; numbers and caveats: [agent.md](agent.md) §6.
- **The headline: routing is 1.000 across three lanes, and the third lane costs the first two
  nothing measurable.** Recall@5 0.667 with the web lane against 0.633 without — one question, well
  inside the 0.200 spread, so "no evidence of harm" rather than an improvement. `web_reach_rate`
  1.000 (4/4). **The agent never over-reached: `web_search` was called on 0 of 30 reference
  questions and 4 of 4 web questions** — answerable from a run file for the first time, because
  `tools_used` landed this phase.
- **The one real cost is `abs-03`, and it is the most interesting result of the phase.** *"What will
  the standard Part B premium be in 2027?"* is a figure CMS has not published. Without the web lane
  the agent abstains; with it, it finds projections written *about* the figure and answers anyway,
  taking abstention accuracy from 1.000 to 0.800. **This is not a routing error** — the web was the
  right lane to try — and the grounding guardrail cannot catch it, because the answer *is* grounded:
  the quoted words really are on the page. What is missing is a check that the source is
  *authoritative for that claim*, which nothing in this repo does yet. Phase 4's per-claim
  provenance is where it belongs. The question was deliberately kept as an abstention to expose
  exactly this, and it did.
- **Phase 1-c, still true:** **complete and measured; the agent had two lanes.** Alongside the reference
  corpus it now queries the vendored CMS plan data — `list_tables` / `describe_table` /
  `query_structured`, DuckDB over the Parquet mirrors — and cites **rows**, byte-for-byte, with the
  SQL it wrote visible in the trace. Routing measured **1.000** and the reference questions did not
  move (0.800 against 0.767, inside the noise band). Design: [relational-tool.md](relational-tool.md);
  numbers and caveats: [agent.md](agent.md) §6.
- **Phase 1b, still true and unchanged by 1-c:** the reference lane is searched by a five-tool
  toolset — stdlib BM25 *and* LanceDB embeddings, with which tools the agent sees a per-run flag —
  it abstains when the question is out of corpus, and it streams its answer and its tool trace to
  the browser. The Phase 0 stub is still reachable behind `create_app(stub=True)` as a baseline.
  Design: [agent.md](agent.md).
- **The headline result: vector beats lexical, and "both" beats either alone.** Deterministic
  retrieval-only, 30 in-corpus questions: BM25 **0.567**/0.416 against vector **0.733**/0.561.
  Through the agent, 35 questions: lexical 0.667/0.650, vector 0.733/0.717, **both 0.800/0.733**.
  plan.md's bar — *"both only wins if it beats each alone"* — is cleared on both metrics, so
  `agent.toolset: both` ships. **The decision was taken on the retrieval-only pair, deliberately:**
  the agent's known spread at fixed config is 0.200 wide, which is larger than the effect, while
  the `vector` runner reproduced to three decimals across two invocations. Read 0.800-vs-0.733 as
  suggestive and 0.733-vs-0.567 as the finding.
- **The two methods fail differently, which is the more useful half.** Vector fixes 7 and regresses
  2; four of the fixes land at rank 1. `hcg-01` — *"What exactly is a deductible?"*, the question
  agent.md §6 predicted BM25 would lose to the rare word *exactly* — goes from not-retrieved to
  rank 1. But `ncd-05` and `pub-10` go the other way, and **six of thirty defeat both methods**.
  That complementarity is the empirical case for `both`; the six mutual misses say the remaining
  gap is not one more index.
- **1b has since merged to `main`** (`c67f952`, PR #17). One thing it left undone and 1-c repeats:
  **new frontend surfaces keep shipping unviewed.** The eval run-comparison view was never opened in
  a browser at 1b, and neither has 1-c's row citation card or its SQL trace step. Every automated
  gate passes on all of them; no human has looked.
- **[technical_highlights.md](technical_highlights.md) is now an index over
  [highlights/](highlights/)** — the mechanisms worth *presenting*, which is deliberately not a job
  any of the four canonical docs has. Four entries (grounding · the test-suite provider guard ·
  missing-vs-stale · model-written SQL), one page each, and the README links them from above the
  status section. **CLAUDE.md now names it**, deliberately outside the four-docs table and labelled
  *presentation, not reference*: it is derived from the docs that own each design, so a change
  updates the owning doc first and the highlight page second.
- **Next up: run `make eval-web` and `make eval-no-web`**, then write the numbers into
  [agent.md](agent.md) §6 as 1b's and 1-c's are. ~74 model calls plus a few Tavily credits. Until
  that happens Phase 2 is *shipped but unmeasured*, which is a state this repo has not been in
  before and should not stay in — every prior lane landed with a number attached.
- **Phase 2 decisions worth outliving the code.** **Tavily, decided rather than measured** —
  `plan.md` had called for a Tavily-vs-Exa A/B, and that paragraph is now amended rather than left
  standing: this phase grades routing, not answer quality, so the comparison would have measured
  the prompt rather than the backend. **One tool, not two** — `read_url` over Tavily's `/extract`
  (the `get_chunk` of this lane) is deferred with a named trigger condition, because a two-tool web
  lane would make a routing number partly about navigation *within* the lane. **No domain
  allowlist** — restricting to cms.gov/medicare.gov would turn the web lane into a slower copy of
  the reference lane and guarantee abstention on exactly the questions it exists for; the domain is
  surfaced on every citation instead. **The SDK over a raw HTTP call**, for one concrete reason:
  `AsyncTavilyClient` accepts an injected `httpx.AsyncClient`, which is what lets the entire test
  suite drive the real client over `MockTransport` with no key and no socket.
- **Two bugs this phase found that were not this phase's.** Both are recorded because each is the
  *second* instance of a mistake this repo has already made once:
  - **`make smoke-web`'s first run scored 11/12 on `citations_resolve`**, reporting two perfectly
    good web citations as fabrications — because the check demanded a resolvable `chunk_id` of every
    citation that was not a row. Phase 1-c made the identical mistake about rows. **A metric that
    punishes a capability for existing reads exactly like a real regression**, and a check that
    enumerates lanes by exclusion has to be revisited by whoever adds one. Same shape found in two
    more places and fixed: `aggregate()`'s `!= "structured_api"` and `GoldSet.in_corpus()`, both now
    `== "reference"`.
  - **A pre-existing Phase 1a streaming bug**: when the grounding validator rejects an answer and
    the retry words the replacement differently, the reader saw the *rejected draft* followed by the
    accepted one. `done` corrected it after the fact, but the visible text mid-stream was the
    ungrounded one. It survived four phases because it needs a retry **and** a materially reworded
    second attempt, and every scripted test retry was byte-identical or a clean extension.
    `TokenEvent` now carries an additive `reset: bool`. This is the case for keeping a rung that
    spends one real call: `make check-all` could not have found it.
- **The `_build_agent` cache-key trap was taken for the third time**, exactly as
  [agent.md](agent.md) §3 predicted it would be. Adding the fourth axis broke three tests that
  pinned `structured=` explicitly and let `web` default, so the `override` applied to an agent
  nobody used. `ALLOW_MODEL_REQUESTS = False` turned it into a red test rather than a bill again.
  There is now a test asserting the property directly, so the fifth axis fails a test instead.
- **Phase 1b design decisions, each with a reason that outlives the code:** **`text-embedding-3-small` at 1536d** (~$0.04 a build; `-3-large` was rejected not on cost
  but because 30 in-corpus questions cannot resolve the difference between two good embedding
  models). **No hybrid search** — LanceDB carries BM25 in the same table and it is deliberately
  unused, because "both" means both *tools registered* with the agent reconciling them, which is
  what the phase is about; a fused ranker would hide exactly that. **The table stores no text** —
  `chunk_id, doc_id, source, vector` only, so the corpus stays the single source of truth and a
  citation cannot be validated against a second, drifting copy. **No ANN index** — at 6,722 rows a
  flat scan is sub-millisecond, and an approximate index would make recall approximate, destroying
  the one property the `vector` eval runner exists for. **Navigation tools (`get_chunk`,
  `list_documents`) are in every toolset** — dropping them from the vector-only run would fold
  "lost the ability to widen a hit" into the lexical-vs-vector number.
- **Measurement protocol, decided before the tool was written** (the previous entry asked for
  exactly this): the headline comes from a **deterministic retrieval-only `vector` runner**, not
  from an agent A/B. It worked — see the result above. No `--repeat` mode was built, so the
  "a single agent run is not evidence" open question below is *unchanged*, not closed.
- **Open questions:**
  - **A single eval run is not evidence, and the repo still quotes single runs.** Unchanged by
    Phase 1b, and now load-bearing in a new place: the three agent toolset rows (0.667 / 0.733 /
    0.800) are one run each, against a known spread of 0.200. Phase 1b routed *around* this by
    deciding on the deterministic retrieval runners rather than fixing it. Either the runner grows
    a repeat-and-aggregate mode or the docs keep saying "one sample" by hand — they do today.
  - **An errored eval question used to leave its lane's denominator, inflating the score.** Found
    while reading Phase 2's first run: three questions died to `Exceeded maximum output retries`,
    and recall@5 was reported as **0.741 over 27** instead of 0.667 over 30 — the number went *up*
    because the run went worse. Introduced by this phase's own `== "reference"` inversion in
    `aggregate()` (the previous exclusion-list form happened to count errors as misses), fixed with
    a regression test. **Every recall number quoted in this repo from a run with ERR rows is
    therefore suspect and reads high**; the 1a and 1b entries below that mention errored questions
    are the ones to re-derive if they are ever load-bearing.
  - **`UnexpectedModelBehavior: Exceeded maximum output retries` is still unexplained and still
    recurring** — 3 of 43 on the web-on run, 0 on the web-off run. Not web-specific on this
    evidence (1a saw 17 of 35 with no web lane at all), but nobody has characterised it.
  - **`request_retries: 5` reduced the TPM 429s; it did not end them, and Phase 1b made it worse.**
    Running `eval-lexical`, `eval-vector` and `eval` back-to-back put **16 of 35** questions into
    `Rate limit reached` on the third, scoring 0.400 recall and 0.400 abstention accuracy — a
    TPM-starved run looks like a quality regression in every column at once (`run_2026-08-16_6`,
    kept in the run directory as the specimen). A pause plus `--concurrency 2` scored 0.800
    immediately after, so **the untried lever from the last entry is now the tried one and it
    works**. What is still missing is anything that makes it automatic: the runner has no pacing,
    and nothing stops the next person running three evals in a row. `make embed` is not implicated
    — embeddings are a separate, far larger allowance. **Unreconciled:** `DEFAULT_CONCURRENCY` was
    raised to 5 on the grounds that the account moved to tier 2, while the 1-c entry's standing
    advice is to run agent evals sequentially because 5 lost 25 of 40 questions. One of the two is
    out of date and only a run on the current tier says which.
  - **`UnexpectedModelBehavior: Exceeded maximum output retries (2)` recurred and is now
    characterised, if not explained.** Two questions in the vector-only agent run died this way —
    the grounding validator's budget, not a 429, despite sharing the ERR column. Both were re-run
    by hand immediately afterwards and **both succeeded**, so it is model nondeterminism producing
    a non-verbatim snippet twice in a row, not anything toolset-specific. Deliberately not fixed:
    raising `agent.retries` would soften the one guardrail whose failures must stay loud. The 1a
    entry below records the same shape at 17 of 35, which remains unexplained.
  - **The judge model changed after the only two answer-correctness numbers were measured.**
    `evals.judge_model` is now `openai:gpt-5.6-terra`; 0.739 and 0.770 were both graded by
    `gpt-5.6-sol`. Neither is reproducible from the repo as it stands. A `make eval-judge` run on
    the current config would settle it and costs 70 calls.
  - **`new_run_id()` collides when two runs start the same day concurrently.** It counts existing
    files at call time, so two runs launched together both claim `run_YYYY-MM-DD_1` and the second
    overwrites the first. A monotonic suffix or a lock would fix it. Related and separate: the date
    in the id is **UTC**, so an evening run is filed under tomorrow — `run_2026-08-15_1` was written
    at 22:08 local on the 14th. Harmless until someone reads a run id as a local date.
  - **`make chunk`'s `--max-chars` / `--overlap-chars` flags override `config.yaml` and are recorded
    nowhere.** The same invisible-override hole the config split just closed, on a smaller scale.
    Either drop the flags or have the manifest record that an override was used.
  - **nvm is installed at `~/.nvm` but not wired into the shell profile**, so an interactive
    `node`/`npm` still resolves to the old v20.7.0 while `make ui-*` (which sources nvm itself)
    gets 22. Adding `export NVM_DIR="$HOME/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"`
    to `~/.zshrc` fixes it; deliberately not done, since it is a change to the machine rather than
    the repo.
  - `plan.md`'s Exchange PUF paragraph still describes two tables as the ones that matter; the
    build fetches three (Service Area as well, for the ZIP→plan mapping). Minor, and the reason
    is recorded in [exchange_puf_data.md](exchange_puf_data.md) — correct it if the paragraph is
    ever touched for another reason.
  - ~~**Eval run records carry no tool trace.**~~ **Closed at Phase 2.** `EvalQuestionResult` now
    carries `tools_used: list[str]` — the tools called, in first-call order, repeats collapsed.
    Deliberately *not* what `routing_correct` is scored on (that reads the citations, because a
    lookup the agent ran and then ignored is not evidence); the value is in the *difference*, which
    makes "searched the web, then answered from the corpus anyway" visible for the first time. The
    original note follows, for the reasoning:
  - **[historical] Eval run records carried no tool trace, so no question about tool *choice* could
    be answered from a run file.** `EvalQuestionResult` has `retrieved_doc_ids` but not the tool sequence. Two
    separate questions have now hit this — "how often does the agent reach for `vector_search`?"
    (while explaining why `both` beats `vector` alone) and "how often does it reach for
    `get_chunk`?" — and both were answered from a handful of hand-captured traces instead of the
    175 agent questions this branch actually ran. A `tools_used: list[str]` on the result would be
    additive and cheap, and **Phase 2's routing slice needs exactly this shape for lanes**, so it is
    close to a prerequisite rather than a nicety.
  - **Commit `b4770e0` carries four lines of `docs/human_worklog.md`** alongside its `smoke.py`
    change, swept in by a `git add -A`. Harmless to the code, but that file is the author's and the
    commit message does not mention it. Worth un-mixing before the branch merges; `git add <path>`
    rather than `-A` is the habit that prevents it.
  - `part_d_spuf` is a Phase 5 source that landed during Phase 0, on request. Nothing consumes
    it yet and nothing should until Phase 3/5 — but it now exists, so a later phase should not
    re-plan the ingestion, only the modelling layer on top of the mirror.

### Phase 2 checklist

Backend (plan.md, Phase 2):

- [x] Web-search tool (Tavily) registered on the existing agent, key read server-side from `.env`
- [x] Tool-choice behaviour — a system-prompt and tool-description problem, not a router component
- [x] Source-type tagging: `source_type="web"` citations, built from the result, never the model
- [x] Routing correctness extended from two lanes to three — `routing_grader` needed **no change**,
      exactly as its docstring predicted; what grew is the question set
- [x] Basic web-result hygiene — URL dedupe, per-domain cap, domain surfaced on every citation
- [x] UI: the `web` badge goes live; `CitationCard.isRow` fixed so a web citation is not rendered
      as a table of invented columns
- [x] **Measured** (2026-08-20). Routing 1.000 across three lanes; recall 0.667 vs 0.633
      without the lane; `abs-03` is the one regression — see agent.md §6

Beyond the plan.md list, because implementation made them necessary:

- [x] Per-run search budget, request timeout, and a single `retry-after`-honouring 429 retry — the
      first lane where one agent run can spend real money
- [x] Third no-provider test guard (`_no_live_web_search`), and the whole client tested through a
      real `AsyncTavilyClient` over `httpx.MockTransport`
- [x] `tvly-` added to `make scan`'s vendor-secret-key detector
- [x] `EvalQuestionResult.tools_used` — closes the "no run file can answer a tool-choice question"
      gap that had been open since 1b
- [x] `TokenEvent.reset` — the abandoned-draft fix (a Phase 1a bug, found by `make smoke-web`)

### Phase 0 checklist

Backend (plan.md, Phase 0):

- [x] HealthCare.gov ingestion — 747 docs (English only; was 803 before the duplicate-id fix)
- [x] Medicare publications ingestion — 83 pubs / 964 pages
- [x] Medicare NCD ingestion — 345 determinations
- [x] Exchange PUF ingestion — 3 tables, plan year 2026 (structured mirror, not a text corpus)
- [x] Part D SPUF ingestion — 7 files, 2026Q2 (structured mirror; Phase 5 source, built early)
- [x] Public-repo guardrail enforced in code — `make scan` (secrets / PII / licensing)
- [x] Chunking step → 6,722 chunks across the three text corpora (`make chunk`)
- [x] Gold eval set, 35 questions (30 in-corpus + 5 abstention; question → expected source-type
  → expected answer)
- [x] Eval dataset loader / schema
- [x] Test tooling (pytest, configured in `pyproject.toml`, wired into `make check-all`)

Frontend (frontend_plan.md, Phase F0):

- [x] `api/models.py` — the frozen contract models, including the SSE union
- [x] `api/app.py` — `/api/health` + stubbed `POST /api/chat`, static mount, SPA fallback
- [x] `frontend/` scaffold (Vite + React + TS + Tailwind v4 + shadcn), `/api` proxy
- [x] `make types` → `frontend/src/api/schema.d.ts` (committed), plus `make types-check`
- [x] Chat page rendering the stub end to end — badges, citation cards, trace panel, abstention
- [x] Eval endpoints over the gold set, plus HTTP-triggered runs with SSE progress

Beyond the F0 list, because implementation made them the cheaper order:

- [x] `POST /api/chat/stream` + `stream.ts` + its Vitest suite (scheduled for F1; see the log)
- [x] `evals/runner.py` with a pluggable answerer — the Phase 1a swap point
- [x] `GET /api/corpus/{doc_id}` citation drill-down against the real corpus

### Phase 1a checklist

Backend (plan.md, Phase 1a):

- [x] PydanticAI `Agent` — model from `config.yaml`, typed deps, structured output, grounding prompt
- [x] Full-text toolset over `data/processed` — `search_corpus` / `grep_corpus` / `get_chunk` /
  `list_documents`, **no database at all**
- [x] BM25 inverted index built in-process at startup from `chunks.jsonl` (~200 ms, 6,722 chunks)
- [x] Structured output populating the frozen contract (answer + citations + claims + `abstained`)
- [x] Chunk → source provenance; citations rebuilt from the real `Chunk`, never from the model
- [x] Grounding guardrail — an output validator, not a prompt instruction
- [x] Step / usage limits from day one (`request_limit`, `tool_calls_limit`, `retries`)
- [x] Tool-call trace captured and surfaced through `trace`, with real arguments and durations
- [x] Eval extended to answer correctness (LLM judge) and groundedness (deterministic)
- [x] Stub endpoint replaced by the real agent; streaming wired through to the UI
- [x] Eval dashboard over real runs

Frontend (frontend_plan.md, Phase F1):

- [x] Stub swapped for the agent — the banner removes itself, driven by `health.stub`
- [x] Abstention wired to the real guardrail
- [x] Trace panel shows the agent's real tool sequence
- [x] Eval dashboard against real runs, with `model` and `config_fingerprint`

Beyond the F1 list, because the real agent made them necessary:

- [x] A working state while the agent searches — several seconds pass before the first token
- [x] Abstentions render through `AnswerBody`, since the agent can abstain *and* cite
- [x] Citation DOM ids scoped by message id — `c1` is unique per answer, not per conversation

### Phase 1b checklist

*Complete as of 2026-08-16, code and measurement.*

Backend (plan.md, Phase 1b):

- [x] Embedding model configured — same model for documents and queries, fixed dimensionality,
  enforced by `VectorIndex.open` refusing a store whose manifest disagrees
- [x] LanceDB wired up, populated from `data/processed` by `make embed`
- [x] `vector_search` registered **alongside** the 1a tools, not in place of them
- [x] Toolset composition as an eval axis — `select_tools(toolset)` + `--toolset`, one runner
- [x] `system_prompt(toolset)` so no configuration is told about a tool it does not have
- [x] `vectors_meta.json` + `make embed-check` — the store is git-ignored but reproducible
- [x] `make embed` run — 6,722 vectors, `vectors@b693b82f4350`, 53 requests, 47 s, $0.03
- [x] Eval comparison across the three configurations — **vector 0.733 > BM25 0.567; both 0.800
  > vector 0.733 > lexical 0.667**, so `both` earned its place and ships

Frontend (frontend_plan.md):

- [x] Run-comparison view — pick two runs, provenance diff, metric deltas, fixed/regressed/unchanged
- [x] `toolset` badge beside the runner badge; `vectors_snapshot_id` in the run detail
- [x] Verified against the running app: `/api/health` reports the store, and a live question put
  `search_corpus` *and* `vector_search` in one trace with five resolving citations
- [x] Chat UI untouched, by design

Not in the plan, added because the code demanded it:

- [x] A **second money guard** in `conftest.py`. `ALLOW_MODEL_REQUESTS = False` is PydanticAI's
  switch and has no bearing on a direct `AsyncOpenAI().embeddings.create()` — so the suite could
  have reached a provider with the developer's own key and still gone green. `openai_embedder` is
  now replaced suite-wide, and consumers call it *through the module* so the patch actually lands.
- [x] `chunker_snapshots()` moved from `evals/runner.py` to `corpus.py`, which gave it a second
  caller with a stronger need: the store records those ids and refuses to open against a corpus
  that no longer matches.
- [x] `.gitignore` gained `data/lancedb/`. The existing `.lancedb/` line is dot-prefixed and
  **does not match** the path `lancedb.md` documents — an ingest following that doc verbatim would
  have left ~41 MB untracked, un-ignored, and in scope for `make scan`.

---

## Log

### 2026-08-19 — Connection ownership, and the highlights doc split

**Did:** walked the whole 1-c branch step by step (read-only), then three follow-ups: the API now
closes the DuckDB connection on shutdown, `technical_highlights.md` gained a fourth entry on the
relational lane, and that file was split into one page per highlight with the README pointing at it.
A `sync-frontend` pass found nothing to do — `schema.d.ts` was already current and every route in
`client.ts` still matches a path in the dump, which is the useful half of that check.

**Decided: the lifespan owns the structured store, so the lifespan closes it.** It is the only entry
on `AppContext` holding an OS resource rather than plain memory — `VectorIndex` has no `close()` —
and `create_app` is called per test and would be called per host in any embedding process, so
leaving it to process exit leaks a connection per app. Closed in a `finally` around the `yield`, so
a failure during the app's lifetime still releases it.

**The fix immediately broke a passing test, which was the actual finding.** The API tests handed the
app the *session-scoped* store fixture, so the first app to shut down closed it for every later
test. Whoever opens a connection closes it — the fixtures were violating that, invisibly, right up
until something started enforcing it. `test_api.py` now has a function-scoped `app_structured` for
anything that stands an app up, and `_use_fixture_stores` says so in its docstring.

**Decided: `technical_highlights.md` is an index, not a document.** It reached ~700 lines and four
entries, at which point the thing it is *for* — handing someone one mechanism to read — was worse
served by one long file than by four pages. Each highlight is now `docs/highlights/<slug>.md`; the
index carries three paragraphs and the headline number per entry. The README links both the index
and the four pages directly, since a reader who wants the SQL guard should not have to scroll.

**Decided: CLAUDE.md names the highlights, but outside the four-docs table.** Adding a fifth row
would have implied a fifth job; the file is *derived* from the docs that own each design, so it is
listed as **presentation, not reference**, with the rule that a change updates the owning doc first
and the highlight page second. That closes the open call the previous entry left for the author.

**Stopped at:** clean, `check-all` green (300 tests), everything but this doc sweep committed.

**Commits:** `369a163`, `aef9196`, `4f84c5a`.

### 2026-08-19 — Phase 1-c: the structured lane, built and measured

**Did:** built the relational lane end to end, to the design in
[relational-tool.md](relational-tool.md). Three tools (`list_tables` / `describe_table` /
`query_structured`) over the vendored Exchange PUF and Part D SPUF mirrors, DuckDB querying Parquet
in place — no load step, 53 ms to open and verify all ten tables, 3-16 ms a lookup. Row citations
carry `source_type="structured_api"` with null `doc_id`/`chunk_id`, the first citations in this repo
that are not chunks. `make puf`, a 503 naming it, a real `structured_api` lane in `/api/health`,
the `structured` axis on eval runs, four structured gold questions, a routing grader, and the
frontend's two rendering branches (cells not prose; SQL not escaped JSON). 299 tests, `check-all`
green, no contract change beyond added fields.

**Measured (the point of the phase):** two agent runs differing only in the lane. Reference
questions 0.800/0.733 with it against 0.767/0.711 without — one question on a set of thirty,
against a known 0.200 spread, so **no measurable cost**. Routing **1.000**: no plan question was
ever answered from prose, which is the failure this project exists to prevent. Structured exact
match 0.875, abstention 0.833 against 1.000. Detail and caveats: [agent.md](agent.md) §6.

**Decided: the citable unit is a recorded result row, not a chunk.** Every row a query returns is
recorded under a query-scoped id (`exchange_puf/2026/plan_attributes#q1.3`), and a citation names
the row plus the cells it used. Query-scoped rather than data-keyed because an aggregate has no
primary key, and the honest claim *is* query-scoped: this query, against this partition, returned
this row. Cells are compared **byte-exactly** — stricter than the chunk path's
whitespace-normalised check, because `'$4,500 '` keeps a trailing space that a model "helpfully"
tidying it would erase from the evidence while the prose stayed right.

**Decided: guarded SQL, not a typed function per question.** The alternative means deciding at
design time which questions this data can answer — the same guess the ingestion layer refused when
it stored every column as `VARCHAR` rather than picking one of the 36 MOOP columns. The guard is
two independent layers, both verified rather than read about: `duckdb.extract_statements` must
yield exactly one `SELECT`, and the connection is sandboxed with `allowed_directories` +
`enable_external_access=false` + `lock_configuration=true`, **in that order and after the views
exist**. Views are lazy, so locking first breaks them; and `allowed_directories` does nothing while
external access is still on — `read_csv('/etc/passwd')` returned the file until it was disabled.
`tests/test_structured.py::test_the_sandbox_is_shut` is the file that keeps that true.

**Decided: two eval axes, not a four-valued toolset.** `toolset` picks how the reference lane is
searched; `structured` picks whether a second lane exists. Folding them together makes six
combinations, three meaningless, and redefines the three names Phase 1b's measurement is recorded
under.

**Rejected: falling back to the committed sample slices when the mirror is missing.** An
Alaska-only answer served as a national one is the "canned output must not look real" failure with
a health question attached. A 503 naming `make puf` instead — the same refusal-to-degrade as a
missing vector store.

**Rejected: typed views over the mirror, and the two typed join helpers** (`lookup_plan_formulary`,
`lookup_service_area`). Both deferred until traces earn them, and written out in
relational-tool.md §14a so they are implementable later rather than merely postponed.

**Dead end, corrected in the design doc:** the plan said `abs-02` (*"is metformin covered under the
Humana Gold Plus HMO formulary"*) was really a Phase 1-c question. **It is not.** The Part D
formulary carries `NDC` and `RXCUI` and *no drug names*, so a question naming a drug needs a
name → NDC lookup — openFDA or RxNorm, i.e. Phase 3. The gold set gained `str-03`, the same question
in the vocabulary the table actually has. The general shape is worth keeping: **this lane answers
questions phrased in the data's own identifiers**, and translating a human's vocabulary into them is
Phase 3's job.

**Dead end: the agent-cache hazard this file already records, taken again.** Adding `structured` to
`_build_agent`'s `lru_cache` key made every agent test build a *structured* agent while the run
under test built a reference-only one, so `override` applied to an object nobody used and the run
tried to reach OpenAI. `ALLOW_MODEL_REQUESTS = False` turned a would-be bill into a red test.
`AgentKit._agent` now derives the key exactly as `stream_answer` does. **Third defaulted argument,
third occurrence** — the next one should probably be a helper rather than a convention.

**Dead end: the test fixture was quietly better-behaved than the data.** `build_mirror` read the
sample CSVs without the downloaders' `nullstr` sentinel, so DuckDB turned every empty field into
NULL and the fixture lost the `''`-vs-`'Not Applicable'` distinction the whole lane is about. Found
by the trailing-space test, not by inspection.

**Two bugs the first eval run found, both fixed before the reported numbers:** `request_limit: 8`
lost `str-01` to `UsageLimitExceeded` (this lane spends 5-7 calls where the corpus spends two;
now 12/16), and `groundedness_grader` scored row citations as fabricated sources — 0.889 where the
guardrail guarantees 1.000 — because a row has no `chunk_id` to resolve.

**Stopped at:** clean, `make check-all` green, nothing committed. Known limits and open calls:

- **Evals for this lane must run sequentially.** Three sweeps were lost to rate limits: at
  `--concurrency 3` ten questions errored, at `--concurrency 5` twenty-five and thirty-three did.
  A structured question costs ~40k tokens against a ~200k/min account ceiling. A run with errored
  questions is broken, not weak — `recall@5 0.033` on one of them is a set that was never answered.
- **`abs-02` should probably be re-authored.** With the lane on, "abstain" and "answer for the one
  plan you can identify" are both defensible, which is not what a gold label is for. It is a coin
  flip today and is the whole of the 0.833-vs-1.000 abstention difference.
- **Four structured gold questions is thin**, and they are unambiguous by construction, so the
  1.000 routing number is the one to distrust first as the set grows.
- **The frontend's two new branches have not been opened in a browser** — same gap the run-comparison
  view had at Phase 1b. `tsc`, `oxlint` and Vitest pass; nobody has looked at a row citation.
- **`make eval` on a fresh clone now needs `make puf`**, because the gold set has structured
  questions. The runner exits naming the command rather than silently dropping them.


### 2026-08-16 (later) — a walkthrough of Phase 1b, and the doc it produced

**Did:** walked the Phase 1b branch step by step with the author, who was reading the code to own it
rather than approve it. No code changed. Three of the mechanisms surfaced during that reading were
written up in a **new document, `docs/technical_highlights.md`** — grounding, the test-suite
provider guard, and the missing-vs-stale asymmetry.

**Decided: `technical_highlights.md` is a presentation artifact, not a fifth reference doc.** Its
job is "which parts of this build are worth *presenting*", which is a different question from any of
the four docs CLAUDE.md names — those answer what to build, how the UI works, what is built, and
what the words mean. Each entry follows one shape: **problem → approach → the alternative that lost
→ evidence → why it presents well**. The last line of each is the one that justifies the file
existing; without it this would just be `agent.md` with worse organisation.

**Decided: the three topics were chosen by the reader, not by the writer**, and that is worth
preserving as the selection rule. Each was picked when the author stopped mid-walkthrough and said
*this is a highlight* — `remember()` plus the output validator, then `fake_embed` plus
`_no_live_embeddings`, then the two vector-store error types. What a reader independently finds
notable is better evidence of what presents well than an author's own sense of what was hard.

**Observed, and it revises a plausible mental model:** in every trace captured this session
(`make smoke`, `make smoke --toolset vector`, and a live Part D question through the app),
**`get_chunk` was never called.** The search tools already return `ChunkHit.text` verbatim, so there
is no fetch-the-body step — the ranked result *is* the body, and `get_chunk` is a recovery move for a
hit truncated mid-definition. The common second step is **reformulating and searching again**, which
is what `agent.md` credits for the agent beating raw retrieval. The live `both` run did the thing
the toolset exists for: one `search_corpus`, then one `vector_search`, then answered from five
citations.

**Stopped at:** clean, nothing uncommitted. Three limits, all pre-existing and none introduced here:

- The observation above rests on **three traces**, and cannot be turned into a number, because
  eval run records store no tool sequence. That was already an open question; it now has a concrete
  use case rather than a hypothetical one.
- The eval dashboard's run-comparison view is **still unverified in a browser** — unchanged from the
  entry below, and worth doing before the branch merges.
- Commit `b4770e0` (a `smoke.py` change) **also carries four lines of `docs/human_worklog.md`**,
  swept in by a `git add -A`. Nothing was lost or altered, but the author's file is inside a commit
  whose message is about something else.

**Commits:** `40a3a62`, `7d5ec27`, `bc1b23e`

### 2026-08-16 — Phase 1b: vector search joins the toolset, and wins on the number that can be trusted

**Did:** built `vectors/` (the embedder seam, a LanceDB store, a committed manifest), registered
`vector_search` beside the Phase 1a tools, made the toolset a per-run flag, added a deterministic
`vector` eval runner and a `--toolset` axis, and shipped the eval dashboard's run-comparison view.
Then measured it. Reference doc: [lancedb.md](lancedb.md); the numbers live in
[agent.md](agent.md) §6.

**Decided, and it is the methodological point of the phase: the lexical-vs-vector call is made on
retrieval-only runners, not on an agent A/B.** The previous entry left this as the thing to settle
before writing the tool, because seven agent runs at fixed config span recall@5 0.600–0.867 and the
effect being looked for is that size. The answer was not "run the agent more times" (which costs 35
model calls a sample) but "take the model out of the measurement": `make eval-retrieval-vector`
embeds 30 questions, ranks, and stops. It reproduced **identically to three decimals** across two
invocations. That is what makes 0.567 → 0.733 a finding rather than a data point, and it cost about
a hundredth of a cent.

**Measured: vector 0.733/0.561 against BM25 0.567/0.416**, and through the agent, lexical 0.667,
vector 0.733, **both 0.800**. plan.md's bar for the phase was explicit — *"both has to earn its
place: it only wins if it beats each alone"* — and it clears it on both metrics, so
`agent.toolset: both` is what ships.

**The more useful half is that the two methods fail differently.** Vector fixes 7 questions and
regresses 2, with four fixes landing at rank 1. The clean case is `hcg-01`, *"What exactly is a
deductible?"* — the question [agent.md](agent.md) had already named as BM25's characteristic
failure, drowned by the rare word *exactly*. Vector retrieves it first, without reformulation. But
`ncd-05` and `pub-10` go the other way, and **six of thirty defeat both methods**. So the ceiling
here is not one more index: the remaining gap is chunking, gold-set phrasing, or reformulation.

**Decided: no hybrid search, though LanceDB offers it in the same table.** "Both" means both
*tools registered*, with the agent reconciling them — a fused ranker would do the reconciling
itself and hide the exact behaviour the phase exists to observe. It would also put a second BM25
implementation beside `agent/bm25.py`, free to drift from the baseline the comparison rests on.
lancedb.md's bullet advertising the feature now carries that as a recorded rejection rather than an
unexplained omission.

**Decided: the table stores no text.** lancedb.md's own §2 sketch spreads the chunk into the row;
the shipped schema is `chunk_id, doc_id, source, vector`. The corpus is already the one source of
truth for text, and a second copy is a second thing to drift — after which a snippet could be
validated against text the corpus no longer contains. It also keeps the store at 40 MB. Related and
more important: **`vector_search` resolves ids back through the same `CorpusIndex` the lexical tools
use**, which is what makes a semantic hit citable at all. `remember()` looks chunks up by id and
silently skips what it cannot find, so returning LanceDB rows directly would have made every vector
citation fail grounding — two layers from the cause, looking like a model problem.

**Decided: no ANN index, exhaustive search.** At 6,722 rows a flat scan is sub-millisecond, so an
IVF/HNSW index buys nothing measurable and costs the one property the `vector` runner exists for:
an approximate index makes recall approximate, and a number that wobbles with the index cannot
settle the question being asked.

**Decided: the prompt composes with the toolset.** `SYSTEM_PROMPT` was one constant asserting that
search matches "on words, not meaning" and naming `grep_corpus` — both false in a vector-only run.
Leaving it would have made the comparison partly a measurement of how well each configuration
copes with misleading instructions. `system_prompt(toolset)` now varies only the search guidance,
and a test asserts no prompt names a tool its toolset does not register.

**Dead end, caught by the guardrail it was about to bypass: `ALLOW_MODEL_REQUESTS = False` does not
cover embeddings.** It is PydanticAI's switch, and `openai_embedder` calls the OpenAI SDK directly
— so the suite could have reached a provider on the developer's key and still gone green, which is
the worst version of this failure. `conftest` now replaces `openai_embedder` suite-wide, and its
two consumers call it *through the module* rather than binding the name at import, because a
`from ... import` would have made the patch silently not apply. The invariant is now "no test
reaches a **provider**", not "no test reaches a model".

**Dead end: three agent evals back-to-back is a rate limit, not a result.** `eval-lexical`,
`eval-vector`, `eval` in sequence put 16 of 35 questions into `Rate limit reached` on the third,
which scored recall@5 0.400 and abstention accuracy 0.400 — **a TPM-starved run reads as a quality
regression in every column at once.** The run is kept (`run_2026-08-16_6`) as the specimen. A short
pause and `--concurrency 2` scored 0.800 immediately afterwards. The previous entry listed lowering
concurrency as "the untried lever"; it is now tried and it works.

**Dead end, characterised but not explained: `Exceeded maximum output retries (2)` recurred.** Two
questions in the vector-only run died on the *grounding validator's* budget rather than a 429.
Re-run by hand immediately afterwards, both succeeded — so it is model nondeterminism producing a
non-verbatim snippet twice in a row, not anything about the vector path. Deliberately not fixed:
raising `agent.retries` would soften the one guardrail whose failures should stay loud.

**Also caught: `.gitignore` had `.lancedb/`, which does not match `data/lancedb/`** — the path
lancedb.md documents. An ingest following that doc verbatim would have left 40 MB untracked,
un-ignored, and inside `make scan`'s scope. Both are now correct and the doc says which. And
`make smoke` gained `--toolset`, because under `both` the agent reaches for `search_corpus` first
on a defined term and does so successfully — so every default smoke run exercised only the lexical
path, and `vector_search` could have broken with all nine checks green.

**Contract:** two additive optional fields, `toolset` and `vectors_snapshot_id` on
`EvalRunSummary`. No route changed, so the hand-written URLs in `client.ts` were not at risk. The
chat UI is untouched, as designed — `TraceStep.tool` already answers plan.md's "the trace shows
which kind of search produced each citation", so no `Citation` field was added.

**Worth knowing before reading an old number:** `Config.fingerprint()` moved, because `config.yaml`
gained `agent.toolset` and a `vectors:` block. **No future run's `config_fingerprint` matches any
historical one.** Unavoidable and intended, but it means pre-1b runs cannot be matched to current
ones that way.

**Stopped at:** clean and verified where it counts. `make check-all` (248 pytest, pyright 0 errors,
tsc, oxlint, 15 vitest, `types-check`), `make scan` clean with every advisory count at baseline,
`make embed-check` reproducing the committed manifest, and `make smoke` / `make smoke-abstain` /
`make smoke --toolset vector` all passing against the live provider. Verified against the running
app: `/api/health` reports the store and its snapshot, and a live question produced a trace
containing both `search_corpus` and `vector_search` with five resolving citations. **The eval
dashboard's new comparison view has not been eyeballed in a browser** — it builds, and the
endpoints it reads return the fields it needs, but the pixels are unverified. progress.md's own
record is that two of Phase 1a's real UI defects were found in a browser and zero by tests.

### 2026-08-15 — a bug the gold set could never catch, and six runs that make 0.700 look like luck

**Did:** fixed the citation-anchor collision in the chat UI and pinned it with the frontend's second
vitest suite, moved the LLM judge to a different model, reordered `_resolve_model`'s comments to
match what the function actually does, and added a fourth skill (`walkthrough`). Separately, six
more agent eval runs accumulated — nobody set out to measure variance, but the run directory now
does, and it revises the previous entry.

**Decided: the citation DOM id is a function, not a template literal written in two places.**
`Citation.id` is unique *within one answer* — every assistant turn numbers its sources from `c1` —
so a four-answer conversation put four elements carrying `id="cite-c1"` in the document. That is
invalid HTML, and `getElementById` resolves it to the first match in document order, so clicking
`[c1]` in the newest answer reliably scrolled to the *oldest* answer's first source. `citationDomId(messageId, citationId)`
in `frontend/src/lib/utils.ts` is now the single place that shape is written, called by both the
card that renders the anchor and the click handler that looks it up — the point being that the two
cannot drift apart, which two matching template literals in two components eventually would.

**Worth naming as a class of bug:** this is invisible to everything the repo measures. The gold set
asks one question per run, so a single-answer conversation is the only shape the eval harness ever
produces, and `groundedness` and `citation_resolution` both read 1.000 while the link went to the
wrong place. It was found by using the app. The Phase 1a entry below records the abstention-panel
bug the same way — **two of the phase's real UI defects were found in a browser and zero by tests**,
which is worth remembering before trusting a green suite as evidence the UI works.

**Also decided:** no jsdom. `utils.test.ts` asserts the invariant as a property of the *string* —
distinct across messages, distinct within a message, deterministic, unique across a whole
conversation — and stays in vitest's node environment. A component-testing stack and a DOM
implementation to cover one pure function is a large dependency for a small property.

**Measured, and it corrects the previous entry: `request_retries: 5` did not deliver "zero
errors".** Four agent runs at the current config: two completed clean, two lost 2 and 4 questions
to `Rate limit reached ... on tokens per min`. The previous entry recorded "defaulting to 3 (~90 s,
zero errors)" off a single run, which was luck rather than a property. The underlying arithmetic in
that entry is unchanged and still the reason — ~210k tokens against a 200k/minute allowance — so
the honest statement is that the retry budget buys headroom, not immunity.

**Measured: recall@5 at fixed config spans 0.600–0.867.** Seven agent runs now exist. The four on
the current config scored 0.733 / 0.667 / 0.800 / 0.600 — mean exactly 0.700, which is the number
the README quotes, arrived at from a single run on the older config. So the headline is not wrong,
but it was never one measurement's to make. **A 0.100 difference between two single runs means
nothing here**, which matters most for the very next thing this repo does: Phase 1b exists to show
whether vector search beats lexical, and the effect it is looking for is the size of the noise.

**Measured: abstention accuracy is an error detector, not a judgment metric.** There are five
abstention questions, so each is worth 0.200 and nothing lands between the fifths. Every run that
finished clean scored 1.000; the two runs that scored 0.400 and 0.200 were the two that lost the
most questions to errors. A dip there should be read as "the run broke", not "the guardrail
weakened" — and since a broken run also drags recall, an ERR-heavy run looks like a quality
regression in every column at once.

**Dead end, unexplained: a run that lost 17 of 35 questions to `UnexpectedModelBehavior: Exceeded
maximum output retries (2)`.** That is `agent.retries` — the *grounding validator's* budget,
exhausted when the model could not produce a citation that passed validation — and it is a
different failure from the TPM 429s despite occupying the same ERR column in the run record. It has
not recurred in the four runs since, and there is no record of what was being tried at the time, so
it is written down as a shape to recognise rather than a diagnosis. If it returns, the validator's
`ModelRetry` messages are where to look.

**Decided: the judge moves to `gpt-5.6-terra`.** It must be a different model from the one it
grades — a judge marking its own homework agrees with itself most confidently where both are wrong
— and `luna` is still the agent, so that constraint holds. The consequence is recorded as an open
question rather than papered over: both existing answer-correctness numbers (0.739, 0.770) were
graded by `sol`, so neither is reproducible from the repo as it now stands.

**Also:** `_resolve_model`'s comment block was reordered so the `AsyncOpenAI(max_retries=...)`
rationale sits with the client construction and the `OpenAIResponsesModel`-vs-`OpenAIChatModel`
warning sits with the return — the code was right, the explanation was interleaved. And a fourth
skill, `walkthrough`, which hands a change set over one step at a time and pauses; it exists
because reading an agent's output as a diff and understanding it are different activities, and the
default reporting shape serves neither.

**Stopped at:** clean, and not verified beyond that. 202 pytest + 15 vitest collect and pass. The
citation fix was confirmed in a browser across a multi-answer conversation, which is the only place
it could be. No eval run has been made since the judge change.

**Commits:** `5b52d95`, `1e1d83e`, `c6c81d6`, `63a5070`, `bc5747d`, `5e48069`

### 2026-08-14 — Phase 1a: the agent, and the first real eval numbers

**Did:** built the Phase 1a agent — `agent/` (stdlib BM25, `CorpusIndex`, the four tools, the
prompt, the grounding validator, the streaming runtime), swapped it in behind `/api/chat`, and
extended the eval slice with three answerers and three graders. Reference doc:
[agent.md](agent.md). **The repo now calls an LLM.**

The numbers, all on the same gold set and the same chunk snapshots:

| runner | recall@5 | MRR | abstention acc. | groundedness | answer correctness |
|---|---|---|---|---|---|
| `stub` (Phase 0) | 0.033 | 0.033 | 0.400 | — | — |
| `bm25` (retrieval only) | 0.567 | 0.416 | — | 1.000 | — |
| `agent` | **0.700** | **0.667** | **1.000** | **1.000** | **0.739** |

**Decided: the eval answerer/grader seam is `async`, and the runner is one semaphore.** The seam was
synchronous from Phase 0, when the only answerer was a canned function. That forced the concurrency
above to be a `ThreadPoolExecutor` with index slots, and it capped the graders: `judge_grader` used
`asyncio.run`, which **raises inside a running loop**, so an async runner would have broken the judge
at runtime rather than at typecheck — on a paid `make eval-judge`, the worst place to find it.
`AnswerFn` and `Grader` are now awaitable, `run_gold_set` is a coroutine, and `_grade_all` is an
`asyncio.Semaphore` plus one `gather`. The two free answerers are `async def` with nothing to await,
which is the honest cost. Net effect beyond tidiness: the HTTP route dropped its `asyncio.to_thread`
hop and the `loop.call_soon_threadsafe` dance around progress events, and the sequential/concurrent
branch collapsed into one path — `max_concurrency=1` *is* sequential, because a one-slot semaphore is
FIFO and `gather` submits in order. Both ordering properties now have tests, against an answerer that
deliberately finishes backwards. `pytest-asyncio` in `auto` mode; only the evals suite is async.

**Decided: `make eval` runs 3 questions at a time, and the number is a measurement.** The runner was
silent and sequential — 284 s with no output, which is indistinguishable from a hang while spending
money. It now prints a line per question (through the existing `on_progress` seam, so the dashboard
is untouched) and takes `--concurrency`, threads rather than an event loop because `AnswerFn` is
synchronous by design. Two invariants keep it safe rather than merely fast: results are assigned by
index so a run record is identical at any concurrency, and `on_progress` is only ever called from
the calling thread, so neither callback needs a lock.

**Dead end: `--concurrency 5`.** It finished in 46 s and failed 16 of 35 questions on
`Rate limit reached ... tokens per min`, dropping recall@5 from 0.867 to 0.433. The binding
constraint is TPM, not connections: one run is ~6,000 tokens, so the full set is ~210k against a
200k/minute allowance — **the gold set cannot honestly complete in under about a minute at any
concurrency**, and asking for more only converts speed into 429s. Fixed by defaulting to 3 (~90 s,
zero errors, recall@5 0.733) and raising the OpenAI client's `max_retries` to 5 via a new
`agent.request_retries` in `config.yaml`, which is deliberately *not* the existing `agent.retries` —
that one governs whether an answer is grounded, this one whether the request arrived.

**Open: recall@5 varies more than the docs admit.** Three agent runs at identical config scored
0.700, 0.867 and 0.733 — a five-question swing out of thirty, from model nondeterminism alone. The
README and the table below quote 0.700 as *the* number; it is one sample. Either quote a mean with
its spread or say plainly that a single run is noisy.

**Decided: the live check is `make smoke`, not a pytest marker.** The suite is guaranteed never to
reach a provider, and that guarantee is worth more than the convenience of `pytest -m live` — a
marker plus a deselect in `addopts` replaces "certain" with "correct as long as two mechanisms stay
in sync." So one live call lives in `scripts/smoke.py` behind its own Make target, alongside
`scan_sensitive.py` — the same animal, and `tests/` is the one place it must not go, since that
directory's stated invariant is that nothing in it reaches a provider. `scripts` joined pyright's
`include` in the same change (it was already clean), so the five downloaders are now checked by
decision rather than by accident. The reasoning that matters more than the mechanism: a failing test means this repo is
wrong, a failing smoke check might mean the provider changed, and a single command meaning either
teaches you to ignore red. It exists because `_partial_answer` parses output as a **real provider
fragments it**, which `FunctionModel` can only approximate — if that breaks, every test stays green
and streaming silently degrades to the final flush. First live run: 138 token events for a
four-sentence answer, so the partial-JSON path is genuinely load-bearing rather than theoretical.
`make types-check` folded into `ui-check` the same way, closing the open question above.

**Decided: the grounding rule is enforced in code, not asked for in the prompt.** Every chunk a
tool returns lands in `deps.seen_chunks`, and an output validator rejects a citation of anything
else, a snippet that is not verbatim in its chunk, or a dangling `[cN]` — handing the model the
reason via `ModelRetry`. The prompt was written *not* to duplicate any of it: whatever a guardrail
can enforce, the guardrail enforces. `groundedness` and `citation_resolution` came back at 1.000,
which is the only value they should ever have — a number below 1.0 is a bug in the validator, not a
score to improve. They are measured anyway, because a guardrail nobody checks is one that has
already stopped working.

**Decided: citations are rebuilt from the corpus.** The model contributes `chunk_id` and `snippet`,
both validated; title, url, `doc_id` and `source_type` are read off the real `Chunk`. There is no
path by which an invented title reaches the browser. Same instinct made `claims` **derived** rather
than requested — `AnswerClaim.text` must be a verbatim substring of the answer, and a model
reproducing its own prose character-for-character is a coin flip that fails the contract validator
when it loses.

**Decided: `answer_question()` is `stream_answer()` drained.** Phase 0's
`test_stream_done_payload_equals_the_non_streaming_response` compared two independent code paths
for equality, which a nondeterministic model makes unassertable by re-running. Rather than delete
the property, it moved into the code: one path, two shapes. The stub still has two paths, so that
test survives for it.

**Decided: three eval runners, one scorer.** `bm25` answers nothing — it retrieves and stops — but
it goes through the same scorer, run file and dashboard, so its `recall@5` is directly comparable
to the agent's. That comparison is the phase's most useful number: **0.567 → 0.700 is what the
agent's query reformulation is worth**, and it separates "the retriever cannot find it" from "the
agent did not look properly", which are different bugs. It costs nothing to run, which is what
makes it the `bm25_b`/`k1` sweep loop.

**Decided: the LLM judge is opt-in and unreachable over HTTP.** `make eval` stays free and
reproducible from the repo; `make eval-judge` is an explicit act. A button that spends money on
every click is the wrong affordance. The judge runs on a *different* model
(`evals.judge_model`) from the one it grades — a judge marking its own homework agrees with itself
most confidently exactly where both are wrong.

**Measured, closing a question chunking.md opened:** [chunking.md](chunking.md) §5 handed forward
the worry that BM25 length normalisation would over-favour the 505 NCD chunks under 200 chars.
**It does not** — sweeping `b` from 0.0 to 1.0 moves recall@5 by at most one question at any `k1`.
That question is closed. The sweep also confirmed §6's claim that the context header is real
lexical signal: 0.567 with it, 0.500 without, and higher MRR at every cell. `bm25_k1` moved 1.2 →
2.0, but read that as "no evidence 1.2 is better here" rather than a tuned optimum — the margin is
two questions out of thirty, which a set this size cannot resolve, and `tests/test_corpus_index.py`
asserts a floor of 0.40 rather than the measured value for exactly that reason.

**Decided: a missing `chunks.jsonl` fails loudly.** The lane reports `configured: false` and
`/api/chat` returns a 503 naming `make chunk`. Deliberately *not* a fallback to the stub: canned
output must never be mistakable for a real answer, which is the whole reason `HealthResponse.stub`
is a boolean. Booting still succeeds, because `make types` imports the app and the eval dashboard
must work without a corpus.

**Rejected:** a BM25 library, again and for the same reasons as before. Also rejected asking the
model for `claims`, and asking it to only cite what it retrieved (that is `seen_chunks`'s job).

**Dead end: `_resolve_model`'s first draft built the wrong model class by hand.** It needs to
construct the model object explicitly regardless — see the `Secrets` dead end above — but the
first draft picked `OpenAIChatModel` on an unchecked assumption. PydanticAI's own inference of a
bare `openai:` string already resolves to `OpenAIResponsesModel` (`infer_model` maps the plain
`openai` prefix to Responses; only `openai-chat:` gets Chat Completions), so this was a
self-inflicted mismatch, not a framework default working against us. `gpt-5.6-luna` rejects tool
calls on Chat Completions outright — *"Function tools with reasoning_effort are not supported ...
in /v1/chat/completions. To use function tools, use /v1/responses"* — a hard 400 on the first tool
call. Fixed by building `OpenAIResponsesModel` instead, which is what inference would have chosen
anyway.

**Dead end: `build_agent()` and `build_agent(None)` were different `lru_cache` keys**, so they
returned two different `Agent` objects — and `agent.override(model=...)` in a test applied to only
one, sending the run to the real provider. Caught only because `ALLOW_MODEL_REQUESTS = False`
turned it into a loud failure instead of a bill. The default is now resolved *before* the cache
lookup. Worth remembering: an `lru_cache` on a function with a default argument has two keys for
the same call.

**Dead end: `FunctionModel` asserts on a streamed request unless given a `stream_function`.** Since
the runtime only ever streams, every scripted test model needs both halves. The streaming half
emits arguments in 17-character JSON fragments deliberately aligned to nothing — that is what
exercises the partial-JSON parsing behind token streaming, and a single-chunk script would have
left the whole streaming path untested while appearing to pass.

**Dead end (found in the browser, not by a test):** the agent can abstain **and** cite — declining
to name a provider while pointing at HealthCare.gov on how to check a plan's directory is a better
answer, not a contradiction. `AbstentionNotice` printed raw text, so a dead `[c1]` sat above a
citation card it did not link to. frontend_plan §5.1's "no citation section" described the Phase 0
stub, whose abstention cited nothing. The panel now renders through `AnswerBody`.

**Also:** `tests/conftest.py` is the repo's first, and exists mainly to set
`ALLOW_MODEL_REQUESTS = False` suite-wide — `make check-all` needs no API key and costs nothing.
Two additive contract fields (`config_fingerprint`, `model` on `EvalRunSummary`) are the only
contract change; the chat UI, the streaming plumbing and the eval dashboard needed nothing, which
was the stated success criterion.

**Stopped at:** clean and verified where it counts. `make check-all` (200 tests, pyright 0 errors),
`make types-check`, `make chunk-check` and `make scan` all pass with every advisory count at
baseline. Verified in a real browser against the live agent: the stub banner gone, a cited answer,
an abstention, the trace showing real tool arguments and durations, citation drill-down, and all
three runners side by side on the dashboard. Two things noticed and **not** fixed, both minor:
`new_run_id()` collides when two runs start the same day concurrently (an agent run overwrote a
`bm25` run mid-session), and `make types-check` is still outside `check-all`.

### 2026-08-14 — CLAUDE.md condensed to invariants; two reference docs split out

**Did:** cut `CLAUDE.md` from ~2,900 words to ~1,300 (line count is flat — prose became tables and
bullets, which is the shape that survives condensing). Nothing was deleted; four blocks moved to
docs that load on demand:

| Moved out of `CLAUDE.md` | Now lives in |
|---|---|
| Command list, Make targets, toolchain pins, pyright/Node/TS gotchas | **new** [development.md](development.md) |
| Config split rationale, the three easy-to-break rules | **new** [configuration.md](configuration.md) |
| Glossary maintenance rules (what qualifies / what an entry must contain) | [glossary.md](glossary.md#maintaining-this-glossary) — the doc they govern |
| Full data-source catalog with URLs | [plan.md](plan.md) already had it; CLAUDE.md keeps only the two *don't* rules |

**Decided:** the test for staying in `CLAUDE.md` is **"would a session that never reads this go
wrong?"** — not "is this true". Rules whose violation is silent and expensive stay (never put a
tunable in `Secrets`, never chunk a structured source, never add a bulk downloader for
NPPES/openFDA, the contract's `abstained` boolean). Reference material a session can look up when
it needs it goes to `docs/` behind a link. The architecture-phase section was the biggest single
win: seven prose paragraphs restating `plan.md` became a seven-row table, with only Phase 1a kept
long because it is the phase being built.

**Kept deliberately:** the whole *Public-repo data guardrail* section, at full strength and under
its exact existing heading — three files link to that anchor
(`data/README.md`, `frontend_plan.md` §8, `scan-sensitive` skill), and it is the one section whose
failure mode is a licensing violation in a public repo rather than a wasted turn.

### 2026-08-14 — the plan is reframed: an agent that grows tools, not a RAG app that grows features

**Did:** rewrote [plan.md](plan.md)'s Phase 1–4 framing. No code changed and no phase moved; what
changed is what each phase is understood to be *adding*. Phase 1 is no longer "RAG-only MVP" but
"the agent itself, over the reference corpus" — the phase that builds the one PydanticAI agent
every later phase registers more tools on. `plan.md`'s new *One agent, more tools* cross-cutting
principle states it: a phase that would require rewriting the agent loop is a sign the phase is
wrong, not the loop.

**Decided (1a):** the agent gets a **small toolset it composes itself** — `list_documents`,
`grep_corpus`, `search_corpus` (BM25), `get_chunk` — not one `retrieve(query)`. A single call
would hide the search strategy inside a ranking function, which is the part of the exercise worth
doing; with narrow tools, a bad query and a recovery are both visible in the trace. It also makes
Phase 2's lane routing a change of degree — the agent is already choosing between tools before a
second lane exists. Cost, accepted: more surface for the agent to get wrong, and the trace becomes
load-bearing UI in 1a rather than 4.

**Decided (1b):** vector search **joins** the lexical tools instead of replacing them behind a
shared `retrieve` interface. The A/B gets messier — the comparison is now lexical-only /
vector-only / both, with toolset composition as an eval axis rather than two runs of one
signature — but "both" is the configuration the product would actually ship, so it should be the
one measured. LanceDB carrying vector and BM25 in one table (see [lancedb.md](lancedb.md)) is what
makes that cheap.

**Decided (2):** web search is an **agent-oriented search API — Tavily or Exa**, chosen by
measuring both on the gold set's out-of-corpus slice, not a scraped SERP. They return extracted
page content with source URLs in one call, which is what per-claim provenance needs; scraping
would mean owning an extraction pipeline unrelated to this project.

**Also:** loop safety moves earlier. Step/usage limits go in with the agent in 1a; Phase 4 adds
cycle detection and a hop ceiling on top rather than introducing the idea.

**Unchanged on purpose:** the frozen API contract, the three lane values (`reference`,
`structured_api`, `web`), the phase order, and every acceptance test. The `reference` lane is
still the corpus — 1a and 1b are two ways of searching it, which is why widening 1b did not touch
the contract.

### 2026-08-14 — configuration splits in two, and the Phase 1a dependencies land

**Did:** added `pydantic-ai` and `pydantic-settings`, then split configuration along the line the
first one exposed: secrets from the environment (`settings.py`), everything else from a committed
`config.yaml` (`config.py`). `ChunkParams` moved into the new config module as part of that. No
agent yet — this is the wiring the agent will sit on.

**Decided:** **the axis is committed-reproducible-input vs git-ignored credential, not
secret vs non-secret.** That reframing is what settles the env-var question, which is otherwise a
matter of taste: a secret *must* come from the environment because it cannot be committed, and
anything that changes an eval result must *not*, because an env var is invisible to git — a run
configured by an unrecorded `AGENT_MODEL=...` cannot be reproduced from the repo, and the score is
worth less for it. So `Secrets` inherits `BaseSettings` and the config models are plain
`BaseModel`s, which have no environment source to accidentally re-enable.

**Decided:** **two accessors, `get_config()` and `get_secrets()`, never composed into one object.**
A chunking run or a retrieval test needs `top_k` and no credentials; composing them would make
every such caller fail when `OPENAI_API_KEY` is unset. Verified by running `get_config()` with the
variable cleared.

**Decided:** **no field defaults in `config.py`; `config.yaml` is mandatory.** A default in Python
beside a value in YAML is two sources of truth that drift silently. The file is committed so it is
always present, and a missing key is an error that names the key. `extra="forbid"` makes a typo'd
key fail at load rather than being ignored.

**Decided:** **`ChunkParams` moved rather than being imported.** `chunking/__init__.py` imports
`pipeline`, so `config.py` importing `chunking.params` is a cycle — the same shape as the
`api/__init__.py` note in the F0 entry below. `CHUNKER_VERSION` deliberately stayed behind: it
describes what the *code* does, not how it is tuned, so it must not live in a file users are
invited to edit.

**Decided:** the model default is `openai:gpt-5.6-luna`, **a floating alias, knowingly.** OpenAI
publishes no dated snapshot for that family (checked against the live account — only the three bare
aliases exist), so a rerun can differ from its baseline. `Config.fingerprint()` exists to mitigate
it: the eval run record should carry it and the resolved model. `tests/test_config.py` keeps the
dated-snapshot rule for every family that *does* publish one, with these three as named exceptions.

**Decided:** `min_length=1` on the API key, and `.env.example` ships it **blank**. A non-empty
placeholder passes a "is it set" check, boots cleanly, and fails with a 401 on the first question —
which is precisely the deferred failure `pydantic-settings` was chosen over `python-dotenv` to
avoid.

**Decided (later the same day, superseding a wrong turn):** **Phase 1a uses no database at all.**
The session first proposed DuckDB's FTS extension — already a dependency, Porter stemming,
query-time `k`/`b` — and recorded a conflict with the LanceDB rationale, which had chosen LanceDB
partly so the 1a lexical baseline and the 1b vector run would share one store. Both were the wrong
frame. **BM25 is a ranking formula, not a storage engine**: an inverted index in a `dict` over the
6,722 chunks builds in 214 ms and answers in 3–4 ms, on the standard library alone. So the choice
was never "which database" — it was a database nobody needed. `plan.md` §1a now says *no database
of any kind* rather than *no vector database*, and `lancedb.md`'s shared-store bullet is marked
superseded: the 1a-vs-1b comparison straddles two systems deliberately, which is not a confound
because both read the same `chunks.jsonl` at the same `snapshot_id` against the same gold set.

**Rejected:** a BM25 *library*. `rank-bm25` is unmaintained and `bm25s` drags in numpy/scipy, and
neither earns a dependency over ~40 lines of stdlib — which also keeps `b`/`k1` directly in hand,
which is what chunking.md §5 hands forward as the thing to tune against the gold set.

**Rejected:** `pydantic-evals`, which arrived inside the full `pydantic-ai` bundle. `evals/runner.py`
is already the pluggable swap point; a second eval framework would pull Phase 1a sideways.
Also rejected a `config.local.yaml` override — it reintroduces exactly the invisible-override hole
this split closes. One-off experiments should be an explicit flag the run record captures.

**Dead end:** `Agent("openai:...")` **does not see `Secrets`.** PydanticAI reads `OPENAI_API_KEY`
from the process environment, and `uv run` does not load `.env`, so the obvious wiring raises
`UserError` even though the settings object loaded the key correctly. The fix is to pass
`OpenAIProvider(api_key=...)` explicitly, which is the better shape anyway — the credential flows
through one audited place instead of ambient global state. Worth knowing before writing the agent.

**Dead end:** two pyright frictions with `pydantic-settings`, both fixed rather than suppressed.
`Secrets()` — the only correct way to call it — is a `reportCallIssue` for a missing argument, so
the field uses `Field(default=...)`, which reads as "required" to pydantic and "has a default" to
pyright's `dataclass_transform`. And the documented `_env_file=None` test keyword is invisible to
pyright, so the tests use an `IsolatedSecrets` subclass overriding `model_config` instead.

**Dead end:** the first draft's fake key in `tests/test_settings.py` was a **blocking**
`cred:assignment` hit. It was a hyphenated phrase that read as obviously fake to a human but began
with a word `PLACEHOLDER_RE` does not know, and that regex anchors at the *start* of the value — so
only a value **beginning** with `placeholder`, `dummy`, `fake`, `test` (etc.) is recognised. The
guardrail catching the assistant's own fixture is the system working; the constant was fixed and
carries a comment saying why for whoever writes the next one. Then this very entry tripped the same
marker by quoting the bad literal — the 2026-07-31 entry below already records the rule that
prevents it (**write the pattern, never the specimen**), which is worth re-reading before
documenting any blocking shape.

**Also:** full `pydantic-ai` was chosen over `pydantic-ai-slim[openai]` on request. The cost is
concrete — it pulls `google-genai`, which pins `websockets<17.0` and downgraded it from 17.0.1 to
16.1.1. Nothing breaks (`uvicorn[standard]` needs only `>=13.0`, and SSE uses no websockets), but
it is a transitive pin from a provider the repo never calls.

**Stopped at:** clean, and verified where it counts. `make check-all` (84 tests, pyright 0 errors),
`make scan`, and `make chunk-check` all pass — the last one reproducing all three committed
manifests with identical `snapshot_id`s, which was the real risk in moving `ChunkParams`. The
`params_sha256` was confirmed byte-identical *before* any downstream file was touched. A live
OpenAI call was made by hand to prove the key path end to end; **no code in the repo calls an LLM.**

### 2026-08-13 — a skill for contract sync, and vitest joins the gate

**Did:** added the `sync-frontend` skill — the procedure for propagating a FastAPI contract change
through codegen into the frontend — and folded `vitest` into `make ui-check`, so the stream-parser
suite now runs as part of `check-all` rather than only on demand.

**Decided:** **the skill is deliberately not about `make types`.** That is one line and needs no
skill; the document exists for the three things around it — the schema diff is the work list (which
is only true because `dump_openapi.py` renders byte-stably, so no line in it is dict ordering), the
seams codegen cannot see, and an empty diff after a real backend change being a *symptom* rather
than a success. The seam that justifies the whole document: `client.ts` hand-writes its URL strings
and the generated `paths`/`operations` types are unused, so a **renamed route compiles clean and
404s at runtime**. Nothing in the toolchain catches it; a by-hand diff of the `paths` keys is the
only defence, so the skill says to do it even when the diff looked small.

**Decided:** **vitest runs in `ui-check`, not only in `ui-test`.** Same shape as the earlier
argument for `tsc`, and stronger: `stream.ts` is the one frontend module with real logic, and the
bugs its tests cover — an SSE frame or a multi-byte character split across a chunk boundary — are
invisible in ordinary use, so the suite is the only feedback that exists. Neither reason `scan` and
`chunk` sit outside `check-all` applies here: it writes nothing and takes about a second.

**Decided:** the skill's stopping rule is CLAUDE.md's "the UI tracks the phases, it doesn't lead
them", stated explicitly because the instinct mid-sync runs the other way. A new response field that
*could* be rendered gets reported and asked about, not rendered; a widened `Literal` surfaces the
question (what does the new lane look like?) rather than an invented answer to unblock the compiler.
The contract was frozen early precisely so a Phase 4 field does not acquire Phase 4's UI today.

**Also:** the F0 UI was run and looked at in a browser, closing the open question the previous entry
left — the pixels are now verified alongside everything beneath them.

**Stopped at:** the skill has not been run against a real contract change. Its §3/§4 claims are read
off the F0 code as it stands, not proven by an actual sync — the first Phase 1a contract edit is
what will test them.

**Commits:** `791b875`, `2dba60c`

### 2026-08-13 — Phase F0: the contract, the stub, and the UI on top of it

**Did:** built the whole frontend half of Phase 0 — the frozen API contract, a FastAPI app serving
canned answers over both JSON and SSE, an eval runner with HTTP-triggered runs, and a React UI
rendering all of it. Phase 0 is now complete on both halves.

**Decided:** **`api/models.py` imports neither `evals` nor `fastapi`, and eval *wire* models live
in `routes/evals.py`.** `evals/models.py` had a standing note to move `SourceType` into the
contract module and import it back; doing so fixes the dependency arrow as evals → contract, which
then forbids the obvious tidy-up of putting `EvalQuestionsResponse` (which needs `GoldQuestion`)
beside the other models. The no-`fastapi` half is the load-bearing one: `tests/test_gold_set.py`
transitively imports the contract, and the gold-set tests should not drag in a web framework.
`api/__init__.py` is docstring-only for the same reason — a single re-export of `create_app` there
would make `import api.models` execute `app.py`, reach `routes/evals.py`, and land back in a
half-initialised `api.models`.

**Decided:** **the SSE discriminant goes inside the JSON payload, and payloads are wrapped.**
frontend_plan §4.5 sketched bare payloads with the type only on the `event:` line. That cannot be
a discriminated union: `openapi-typescript` has nothing to narrow on, so `stream.ts` would need a
hand-written string→type map — the duplicated contract CLAUDE.md exists to prevent — and `step`
and `done` are not structurally distinguishable as bare objects. The `event:` line is kept for
`curl` readability and *derived* from the payload, so the two cannot drift. Getting the union into
`openapi.json` at all needed a `StreamEventEnvelope` model (the `responses=` metadata takes a
model, not an annotated union) plus a response class declaring `text/event-stream` at class level;
without the latter FastAPI files the schema under `application/json` and it looks correct while
being wrong.

**Decided:** **streaming was pulled forward from F1 into F0.** Three reasons, and the first is the
one that decides it: `/api/chat/stream` is the only non-artificial place to hang the envelope, so
the alternative was a fake schema-only endpoint returning something nobody wants. The plan also
calls the stream parser "the one place a subtle bug hides", and debugging it beside a brand-new
agent in 1a is the worse ordering. It cost a generator, since it is the same canned response
either way.

**Decided:** **eval runs are triggerable over HTTP (settling frontend_plan §10.2), and the
answerer is a parameter.** At Phase 0 that parameter is the chat stub, so metrics are *genuinely
computed* against canned answers rather than faked — recall@5 lands at 0.033 and abstention
accuracy at 0.40, which is the honest picture — and every run record carries `runner: "stub"`,
which the dashboard renders as a badge. Phase 1a swaps one function and the API, the storage
format, and the UI are untouched. Progress events are **buffered and replayed** rather than pushed:
the stub finishes 35 questions in under a millisecond, so a push-only stream would routinely
complete before the browser opened it and the dashboard would show an empty run that had in fact
succeeded. That stays correct when 1a makes a run slow.

**Decided:** **`check-all` runs the frontend gate but skips it with a message when
`frontend/node_modules` is absent.** frontend_plan §7 said to fold `tsc` and lint in
unconditionally, which breaks the repo's primary command on a fresh clone. Skipping is not the
softer option here — `tsc` is a correctness check, unlike `scan` (slow) and `chunk` (writes files),
so it belongs in `check-all` rather than beside them.

**Decided:** **TypeScript is pinned to `~5.9`, against the 6.x `create-vite` now scaffolds.**
`openapi-typescript` peer-requires 5.x, and the codegen is the mechanism that makes a Pydantic
change a TypeScript compile error — frontend_plan §4.3's "single highest-leverage choice". Trading
that for a TS major nothing needs is the wrong trade. Also swapped ESLint for `oxlint` (what the
template ships now; same `npm run lint`, no config) and skipped `sse-starlette` entirely — §7
marked it optional and the frame writer turned out to be three lines.

**Decided:** the stub's two citations have deliberately *different shapes* — one with an external
`url` and a score, one with neither — and its trace covers all four `kind` values with one step
leaving every optional field unset. A canned response that fills every field cannot catch a
component that renders `undefined ms`. Trigger words (`abstain`, `lanes`) select the abstention and
all-three-lanes variants rather than React fixtures, so those paths go through the real serializer
and the real generated types.

**Rejected:** a lazy corpus index. Measured the eager load at ~23 ms and ~12 MB for all 2,056
documents, which is less than the cost of writing the lazy path. Also rejected a `doc_id →
byte-offset` sidecar: it saves the memory and buys a second file format, a rebuild step, and a
seek per request. Chunks are deliberately never loaded by the API — `chunks.jsonl` is git-ignored
and a fresh clone has none, so `/api/health` reads the committed manifest instead.

**Dead end:** the first eval-run test monkeypatched `runner.EVAL_RUNS_DIR` and still wrote into the
real `data/eval_runs/` — default arguments bind at definition, so `runs_dir: Path = EVAL_RUNS_DIR`
never saw the patch. Caught only by listing `data/` afterwards, not by a failing assertion. Every
persistence function now takes `Path | None` and resolves through `_runs_dir()` at call time. Worth
remembering: a test that quietly writes into the repo's data tree is exactly what the public-repo
guardrail exists to stop, and nothing in the suite would have said so.

**Dead end:** Vite 8 binds `[::1]` only, so `curl 127.0.0.1:5173` is refused while
`localhost:5173` works — an ugly split, and inconsistent with the uvicorn side, which binds
`127.0.0.1` deliberately because there is no auth. `server.host` is now explicit.

**Dead end:** the SPA fallback's `assets/` carve-out was written as `path.startswith("assets/")`,
which misses a bare directory request — Starlette passes that through as `assets` with no trailing
slash, so `/assets/` fell through to `index.html` with a 200. Compared as a whole path segment now,
with the bare-directory case in the test parametrisation.

**Dead end:** Homebrew on this machine has a stale formula index (no `node@22`, and it errors on
the macOS version), so the Node 22 upgrade went through a `~/.nvm` install instead. That is
self-contained and left the existing `/usr/local/bin/node` v20.7.0 untouched — but **nvm was
deliberately not added to the shell profile**, so an interactive shell still gets v20.7.0. The
Makefile's `ui-*` targets source nvm themselves, so `make` works either way; a bare `npm` in a new
terminal does not. See *Open questions*.

**Stopped at:** clean. `make check-all` (64 pytest + tsc + oxlint), `make ui-test` (11 vitest),
`make types-check`, and `make scan` all pass, with every advisory scan count exactly at baseline —
the `.gitignore`-before-`npm install` ordering held and `node_modules` never entered scan scope.
Verified end to end over HTTP: the proxy, the SSE stream through it, a browser-triggered eval run,
citation drill-down against the real corpus, and the SPA deep link in both dev and single-process
mode. **The rendered UI itself has not been eyeballed in a browser** — everything below the pixels
is verified, the pixels are not.

### 2026-08-13 — chunking, and the duplicate doc ids it surfaced

**Did:** built the chunking step — `src/health_coverage_navigator/chunking/` (params, models,
splitter, per-source strategies, pipeline, CLI), `tests/test_chunks.py` (17 tests), `make chunk` /
`make chunk-check`, and [chunking.md](chunking.md). Also added `corpus.py` as the shared text-corpus
vocabulary, and fixed a corpus defect the work exposed. Phase 0's backend is now complete.

**Decided:** **doc ids come from the raw post's filename stem, and the Spanish pages are dropped.**
`healthcare_gov` had 803 records but only 747 distinct ids: the Spanish content object at
`/es/hawaii/` self-reports `url: "/hawaii/"` **and** `lang: "en"`, so `id = slugify(url)` collapsed
all 56 state pages into pairs, and the existing `--lang` filter was a no-op that could never have
caught it. Both fields now derive from the stem (804 stems, zero collisions) and `--lang` defaults
to `en`. Chunking forced the issue — two records sharing a doc id make a citation label ambiguous
and a chunk's parent unidentifiable — but the bug predated it and would have quietly scored
duplicate-text retrievals in every eval from here on. Deduping in the chunker was rejected: files
sort `es_hawaii.json` **before** `hawaii.json`, so "keep first" would have silently stamped the
Spanish title onto 21 English state pages. `normalize()` now raises on a duplicate id rather than
trusting the caller.

**Decided:** **`chunks.jsonl` is git-ignored; `chunks_meta.json` is committed.** This inverts the
"processed is committed" rule and is the first exception on that side of the tree, so the reasoning
matters. Size (~7 MB) is not it. The real arguments: chunks are a pure offline function of
committed inputs (unlike `corpus.jsonl`, which needs network to rebuild) and rebuild in about a
second; they churn end-to-end on every parameter tweak, and git deltifies reordered JSONL badly.
The decisive one is that `scan_sensitive.py` scopes licensing markers to `data/processed/*`, so
committed chunks would **double-count every narrative CPT/HCPCS mention** — triple inside overlap
regions — while adding zero new licensing surface, since chunk text is a verbatim subset of corpus
text. The advisory baselines would have stopped being a number about the corpus. What replaces the
artifact is the manifest plus `test_chunks_match_committed_manifest`, so reproducibility is checked
rather than asserted — the same standard `part_d_spuf`'s CRC32 set.

**Decided:** **overlap is 320 chars because the longest gold snippet is 279.** Not a round number
picked by feel. Combined with snapping the next chunk's start **backward only** — the greatest
boundary at or *before* `end - overlap`, never after — realized overlap is always ≥ 320, so any
passage under that length is wholly inside at least one chunk. That turns
`test_gold_snippets_survive_chunking` from an observation into a guarantee. "Snap to the nearest
boundary" is the obvious-looking version of that code and silently breaks it; the splitter says so
in a docstring.

**Decided:** **NCD sections are split on `## ` and small sections are not merged**, even though 505
of 2,256 NCD chunks are under 200 chars (339 of them `Benefit Category`). `build_text()` writes
those headings specifically so the chunker can keep the section name as a citation label; merging
discards it. `Benefit Category` is always first, so it could only merge *forward*, after which the
heading would misdescribe several KB of clinical indications. And the sections are semantically
atomic — a statutory benefit category, a cross-reference pointer. The residual risk is BM25
length normalization over-favouring short chunks; that is a **Phase 1a `b`/`k1` question to measure
against the gold set**, and chunking.md says so explicitly to stop it being relitigated as a
chunking bug.

**Decided:** the contextual header (`NCD 30.3 > Acupuncture > Indications...`) is a **computed
property**, not stored text. Baking it in would end the verbatim-slice contract that the offsets
test and Phase 4's per-claim highlighting both rest on, and would make a citation-format tweak a
full re-chunk. Keeping the pieces as bare fields instead would push header construction into Phase
1a, 1b and the eval runner separately, where they would drift. Phase 1b's line becomes: embed
`retrieval_text`, **store `text`** — so the `text` key lancedb.md's loader expects is unchanged.

**Decided:** medicare_pubs pages are split to the same budget as everything else rather than kept
one-page-one-chunk. 197 of 964 pages exceed 2,400 chars, and one index and one gold set span all
three corpora — letting one corpus average 2× another's chunk size would make the Phase 1a-vs-1b
comparison partly a measurement of chunk size. Nothing is lost, because `page` is metadata: the
citation is *Medicare & You 2026, p. 31* either way. The printed running header stays **in** the
text (stripping it would break the verbatim contract for ~40 chars) and is *promoted* to `heading`,
which lifts heading coverage from 192 pages to 641.

**Decided:** characters, not tokens, with no tokenizer dependency. Phase 1a is lexical; Phase 1b's
embedding ceiling is 27× the budget, so a real token count buys only a cost estimate that
`n_chars / 4` gives within ~10% — and one baked into a committed artifact is invalidated by any
model change.

**Dead end:** the first cut of the manifest-writing path rewrote `chunks_meta.json` on every run,
because the timestamp always differs. A "re-runnable" step that dirties the tree every time is one
nobody re-runs, so the write is now gated on the content *excluding* `chunked_at`. Verified by
mtime across two runs.

**Dead end:** `BuildResult` as a plain class with an `__init__` had pyright widening
`self.source` to `str`, which then failed at all six `CorpusName` call sites downstream. A frozen
`@dataclass` with real annotations fixed it and deleted the boilerplate. Worth remembering: an
inferred attribute is not the same as a declared one.

**Deviation from the plan:** the per-source path helpers (`corpus_path`, `chunks_path`,
`chunks_meta_path`) live in `corpus.py`, not `paths.py` as planned — `paths.py` cannot name
`CorpusName` without importing `corpus.py`, which imports `paths.py`. `paths.py` keeps the
directory constants; `corpus.py` owns everything typed by corpus name.

**Stopped at:** clean. `make check-all` (32 tests, 0 pyright errors), `make chunk-check`, and
`make scan` all pass. One baseline moved: `pii:phone` 39 → 37, because dropping the Spanish state
pages removed two duplicate agency numbers — a corpus that shrank, not a change in what it carries.

### 2026-08-03 — pyright never actually checked `tests/`

**Did:** added `"tests"` to `[tool.pyright]`'s `include` in `pyproject.toml`, fixed the two real
type errors that surfaced once it did (both in `tests/test_gold_set.py`, both `CorpusName | None`
/ `str | None` fields being indexed/passed as if narrowed to non-`None`), and made `make test` run
`pytest -v` so per-test names show by default instead of dots.

**Decided:** the fix for the type errors is `assert q.corpus is not None` / `assert
q.expected_snippet is not None` right after entering each `gold.in_corpus()` loop, rather than
loosening the `GoldQuestion` model. The fields are genuinely `Optional` — `None` for abstentions,
required otherwise — and that's enforced at runtime by the model's validator, not by the static
type, so pyright has no way to know a loop born from `in_corpus()` excludes the `None` case. The
asserts double as a real guard (if the validator were ever bypassed via `model_construct`, this
fails loudly instead of a confusing `KeyError`), not just a type-checker appeasement.

**Dead end (a real gap, not just noise):** `make check-all`'s bare `uv run pyright` reported 0
errors the whole time these bugs existed, because `pyright`'s project config only scanned
`include = ["src"]` — 5 files, none in `tests/`. The bug was visible only because the file
happened to be open in the IDE, which type-checks whatever's open regardless of project config.
`check-all` is the command this repo trusts as its correctness gate; a silent blind spot in it
is worse than a caught bug. Confirmed via `uv run pyright --stats` before (5 files checked) and
after (6 files checked) the `include` change.

**Stopped at:** clean. `make check-all` now checks 6 files, 0 errors; all 15 tests still pass.

### 2026-08-03 — Phase 0 gold eval set (35 questions) + eval package + pytest

**Did:** authored `evals/gold/questions.yaml` — 10 questions per text corpus
(`healthcare_gov`/`medicare_ncd`/`medicare_pubs`) plus 5 out-of-corpus abstention cases, backed
by `health_coverage_navigator.evals.{models,loader}` (Pydantic schema + YAML loader) and
`tests/test_gold_set.py`. Also added `paths.py` (the package's first shared module besides
`__init__.py`), pytest as the project's first configured test tool, and a `make test` target
folded into `check-all`.

**Decided:** gold labels anchor on `corpus.jsonl` **doc ids**, not chunk ids — `expected_doc_ids`
plus a verbatim `expected_snippet` from the primary doc's `text`. This is what unblocks the eval
set ahead of chunking (see the corrected *Next up* note above): doc ids are stable regardless of
how chunking eventually splits a document, and the eval runner can map a retrieved chunk to its
parent doc for `recall@k` today. When chunk-level scoring is wanted later, the snippet already
pins the exact passage, so no relabeling pass is needed. The corpus-dependent invariants that
matter — do these doc ids exist, is the snippet actually verbatim, is the target not a `RETIRED`
NCD, do the distribution counts hold — are enforced by `tests/test_gold_set.py` against the live
corpus rather than trusted by inspection; the structural invariants (abstention shape, required
fields) are enforced by a pydantic `model_validator` in `evals/models.py` instead, since those
hold independent of the corpus.

**Decided:** `expected_doc_ids` is **any-of**, not all-of. The three corpora are genuinely
redundant — the SNF days-21-100 coinsurance figure and the "homebound" definition each appear
verbatim in multiple publications — and penalizing a retriever for finding the equally-correct
one would measure nothing real. `recall@k` counts a hit if any listed doc lands in the top *k*;
`MRR` uses the rank of the first hit. Questions that require **combining** two documents are
Phase 4 multi-hop territory, out of scope here.

**Decided:** add 5 abstention questions beyond the ~30 asked for. Phase 1a's acceptance test
explicitly requires abstaining on out-of-corpus questions, and `abstained` is a first-class
boolean in the frozen API contract specifically so this is gradeable without parsing answer text
— an all-in-corpus gold set would leave that guardrail untested. Each abstention carries
`becomes_answerable_at_phase` (provider lookups and formulary checks turn `3`; time-sensitive
web-lane questions turn `2`; a future plan-year figure and an out-of-domain question stay `null`
permanently) so a later phase's eval run doesn't score a now-correct answer as a false abstention.

**Decided:** difficulty is graded and labeled (8 easy / 14 medium / 8 hard across the 30
in-corpus questions) and is about the **phrasing gap** to the source wording, not about how
counter-intuitive the correct answer is — two of the "easy" NCD questions (`ncd-09`, the insulin
syringe rule; `ncd-10`, screening vs. diagnostic mammography) have surprising or easy-to-overclaim
answers precisely so the set can catch an agent that pattern-matches to a plausible "yes" instead
of grounding in what the source actually says.

**Decided:** at most 6 of the 30 in-corpus questions may be `volatile: true` (year-specific dollar
figures); only 2 are (`pub-02`'s SNF coinsurance, `pub-03`'s Part D late-enrollment penalty
math), both pinned to `plan_year: 2026`. Preferring rule-shaped facts (a 6-month enrollment
window, a homebound definition) over dollar amounts keeps the set from rotting when 2027 figures
land.

**Decided:** the file lives at `evals/gold/questions.yaml`, not under `data/`. `data/` is the
vendored-corpus tree the licensing scanner's path-scoped markers apply to; this is hand-written
prose *about* the corpus, and the `part_d_spuf` dead end (explanatory text written into a file
under `data/` tripping a blocking marker) was reason enough not to repeat the pattern here. YAML
over JSONL specifically for this file: it's hand-authored and human-reviewed, unlike the
generated `corpus.jsonl` files, and comments plus readable multi-line blocks make a label change
a reviewable diff instead of an escaped-`\n` blob.

**Dead end (caught by the test suite, not by inspection):** the first draft of `hcg-05` cited
`appeal-insurance-company-decision_external-review` for a sentence that actually lives in the
sibling `appeal-insurance-company-decision_appeals` doc — an easy mistake with two docs this
similarly named and titled. `test_expected_snippet_is_verbatim` caught it immediately. A second,
subtler miss on `pub-03`: the snippet's leading "The" was typed lowercase against a doc that
capitalizes it — same test, same immediate catch. Both are the reason the plan required this
test rather than trusting hand-verification: a hallucinated citation is exactly what a language
model (or a human skimming quickly) produces most confidently.

**Glossary:** added `Medigap Open Enrollment Period`, `benefit period`, `grace period`,
`homebound` (upgraded from a passing mention to a real entry), `national base beneficiary
premium`, `creditable (prescription drug) coverage`, `LDCT`, `cLBP`, and `AHI / RDI` — every term
the new questions introduce that the glossary didn't already carry.

**Stopped at:** clean. `make check-all` (ruff, format-check, pyright, pytest — 15 tests) and
`make scan` both pass with no new advisory hits; the loader's distribution summary matches the
counts above.

### 2026-08-02 — NPPES and openFDA are API-only; no bulk downloads

**Did:** recorded the decision not to bulk-download NPPES or openFDA, and swept the docs and the
scanner for claims that assumed otherwise.

**Decided:** both are used live through their APIs and **never vendored**. Provider lookup and
drug-label lookup are inherently one record at a time, so a 4 GB NPPES mirror (or the openFDA
label dump) would buy storage cost and a staleness problem in exchange for nothing the API
doesn't answer fresher. This closes the bulk-source list at five.

**Decided:** the consequential downstream effect is on the guardrail, not the ingestion. The
`pii:npi` baseline in `sensitive_baseline.toml` was annotated "expected to rise when NPPES lands
in Phase 3 — provider NPIs are FOIA-disclosable and cleared to vendor", and `scan_sensitive.py`
justified its whole advisory PII tier partly on that. Neither is true now: **no provider-level
data will ever reach a scanned file**, so `pii:npi` is expected to stay at zero permanently and
a nonzero is a genuine "go and look" rather than a baseline to ratchet up. That is a
strengthening of the guardrail and is now written down as such — it would have been easy to
leave the old note in place and quietly accept a future ratchet.

**Rejected:** keeping a trimmed NBER "core" NPPES mirror as a dev fixture. With no bulk
ingestion at all there is nothing for it to be a fixture *of*; Phase 3's synthetic fixtures
cover the testing need without vendoring real practitioner names. The **NBER** glossary entry
was removed rather than corrected, since the term no longer appears anywhere in the repo.

### 2026-08-02 — Part D SPUF, fetched by byte range

**Did:** added the fifth bulk source, `part_d_spuf` — the quarterly Part D formulary files —
with a guide in [part_d_spuf_data.md](part_d_spuf_data.md). Requested explicitly, ahead of the
phase it belongs to.

**Decided:** fetch **individual zip members over HTTP range requests** instead of downloading
the published file. The container is 2.49 GB and holds ten nested per-file zips; the seven we
want are 9.4 MB, and the pharmacy-network file we don't want is 92% of the weight. So the script
reads the zip central directory over HTTP and pulls only the members it needs. This is a real
departure from the other four downloaders and worth the ~90 lines because the alternative is a
250x transfer cost on every refresh — the kind of thing that gets a pipeline run once and then
quietly abandoned. What makes it trustworthy is the **CRC32 in the central directory**: every
member is verified against it before being written, so a truncated or mis-offset range fails
loudly rather than landing as plausible garbage. A range answered `200` instead of `206` is a
hard error, never a fallback — silently streaming 2.49 GB is the exact failure this avoids.

**Decided:** the committed sample is anchored on `CONTRACT_ID` by a **seed + top-up** rule, not
by a single filter. `exchange_puf`'s "one state" approach cannot transfer: `STATE` is populated
only for Medicare Advantage rows, every standalone PDP leaves it blank (they are region-coded),
and Alaska — that source's default — has zero rows here. Seed takes the smallest PDP and
smallest MA contract so both plan shapes appear; top-up then adds the smallest contributor for
any file the seed would leave empty. The top-up is not tidiness: indication-based coverage names
only **three contracts nationally**, so no seed hits it by chance, and a 0-row fixture cannot
test a join. The rule is computed each run rather than hardcoded, since a contract can stop
being offered between quarters.

**Decided:** treat the source as **Latin-1**. CMS documents these files nowhere as anything but
text, and six of the seven are pure ASCII — but plan-information carries three Spanish plan
names (`Óptimo Plus`, `Freedom Máximo`, `Community y Más`) that make a strict UTF-8 read raise.
ASCII being a subset of Latin-1 means decoding all seven that way is exact, not merely tolerant;
no `errors="replace"`, which would have silently corrupted those three names. The mirror is
therefore lossless in content while transcoding to UTF-8, and that distinction is written down
rather than left implicit.

**Decided:** the `licensing:hcpcs-shaped` scanner hit is an allowlist case, not a regex fix. A
Medicare `CONTRACT_ID` is a letter plus four digits (`H1671`), which is *exactly* an HCPCS
Level II code — and `H`/`R`/`S` are all real HCPCS letters, so the two are indistinguishable by
shape. Loosening the detector would hide real `G0465`-style tokens in the other corpora; what
disambiguates is context, so the entry is scoped by path and any other letter still fires.

**Rejected:** downloading the whole container and extracting locally — simpler and matches
`download_exchange_puf.py`, but pays 2.49 GB to read 9 MB. Also rejected wiring up the
pharmacy-network file (2.29 GB across six parts needing reassembly, and nothing before Phase 5
reads it) and fetching pricing by default (191 MB); pricing is defined and opt-in via `--file`.

**Dead end:** the first `_meta.json` `license_note` spelled out which code sets the source does
*not* carry, and tripped the scanner's blocking `CDT` marker — prose about the guardrail,
written into a committed file under `data/`, where the markers apply to the text itself. Reworded
to describe the position without naming the code sets; the named version lives in the script
docstring and the data guide, both outside `data/`. Worth remembering before writing any other
explanatory string into a committed data file.

**Dead end:** the first cut of the 304 short-circuit checked whether the extract existed *on
disk*. A member that failed after extraction but before it was measured left a file behind that
no catalog entry vouched for, so the next run's 304 skipped it permanently — the file was
stranded and `normalize()` silently ignored it. Fixed twice over: "held" now means on disk **and**
in `catalog.json`, and extraction writes to `.part` and renames only after measuring, so a
failure leaves nothing a later run can trust.

**Stopped at:** clean. Verified end to end by wiping `data/{raw,processed}/part_d_spuf` and
rebuilding from nothing — all seven members reproduced with identical sha256, CRC32, byte
offsets, and row counts, and byte-identical sample files. Nothing consumes this source yet, by
design.

### 2026-08-01 — Exchange PUFs + vector-backend choice

**Did:** added the fourth bulk source — the Exchange PUFs (Plan Attributes, Benefits & Cost
Sharing, Service Area for plan year 2026) — with a guide in [exchange_puf_data.md](exchange_puf_data.md).
Chose LanceDB for Phase 1-b and wrote the reasoning down in [lancedb.md](lancedb.md) before any
code exists. Promoted `/wrap-up` from a command to a skill so it can trigger on intent, not only
on the slash form. Closed out two long-standing drifts in `plan.md`: it now names the MCD
Coverage API as the NCD route (superseding the bulk ZIPs it had described since the original
survey) and LanceDB as the settled Phase 1-b store.

**Decided:** `processed/` no longer means one thing. The three text corpora emit an app-ready
`corpus.jsonl`; `exchange_puf` emits a **lossless mirror** — every column `VARCHAR`, values
byte-for-byte, no trimming, no type coercion. [data/README.md](../data/README.md) now names both
kinds rather than letting this source quietly stretch the old definition. The reason is that
every "numeric" column here is publisher-formatted text (`'$450 '`, `'70.88%'`, `'Not
Applicable'` beside an empty field, which mean *different* things), and that Plan Attributes
carries **36 max-out-of-pocket columns** — choosing which one is "the" MOOP needs real query
requirements from Phase 3/5, and guessing once at ingestion is worse than not guessing. DuckDB's
CSV reader would have collapsed the empty-vs-`Not Applicable` distinction by mapping empty
fields to `NULL`, so `nullstr` is overridden to a string that cannot occur in a CMS PUF.

Service Area is fetched even though `plan.md` names only two PUFs: without the ZIP-to-area
mapping, `ServiceAreaId` cannot answer the plan's own canonical structured-lookup example
("plans in ZIP 30076"), and the table is 44 KB.

Committed a one-state (AK) full-column sample instead of the real files — Benefits & Cost
Sharing alone is a 375 MB CSV — extending the manifest-not-blob bargain `medicare_pubs` already
made. The sample is **regenerated and re-scanned on every `normalize()`**, never hand-curated,
so a future plan year that introduces code-bearing text into Alaska's data fails the run rather
than resting in a stale fixture nobody re-checks. The full PUFs do contain CPT/CDT numbers in
issuer-authored free text (249 lines) — narrative reference, not a redistributed code table, and
git-ignored regardless; the only question that mattered was the sample, which is clean.

For Phase 1-b: **LanceDB**, on two grounds that outrank raw scale at a few thousand chunks —
it holds vector *and* BM25 search in one table, so the 1-a lexical baseline and the 1-b vector
run share a store instead of the comparison straddling two systems; and its versioned writes let
an eval score be pinned to the corpus/embedding snapshot it was measured against. Embeddings are
computed by us and handed over as plain vectors (LanceDB's registry never calls OpenAI on our
behalf), which keeps bulk ingestion eligible for the Batch API discount.

**Rejected:** Chroma — comparable setup cost, but in-memory-first and weaker on the
eval-reproducibility angle. pgvector — the better long-term fit *if* Phase 5 puts the PUFs in
Postgres rather than DuckDB, but not worth operating a server in Phase 1-b. Qdrant/Weaviate/
Pinecone — a server before we need one, or a managed service that reopens the BAA question,
which is the same reason OpenRouter was ruled out earlier: health-domain data should not leave
the machine. Also not fetched: the Rate PUF and five other tables (Phase 5 territory; the URL
pattern is uniform, so adding one is a dict entry).

**Commits:** `a0b6147`, `813123c`, `5afcbdd` (the `plan.md` and `glossary.md` corrections above
are uncommitted)

### 2026-07-31 — sensitive-data scanner

**Did:** replaced the ad-hoc, per-source licensing check with one scanner
(`scripts/scan_sensitive.py` + a `scan-sensitive` skill) covering all three halves of the
public-repo guardrail — credentials, PII/PHI, licence-restricted content — across every file
that is or would become public. Runs clean today; baselines recorded.

**Decided:** three severities, not two. The NCD session had already established that a check
which fails on legitimate narrative `CPT` mentions "would just get switched off"; that lesson is
now the scanner's architecture rather than one function's local behaviour. **Blocking** exits 1
with no judgement call available, **advisory** compares against a recorded count so a *jump* is
reported rather than a nonzero, **allowlisted** suppresses but still names and counts the
suppression. The advisory tier is not a softer blocking tier — it exists because this corpus
genuinely contains code mentions and agency contact details, and because Phase 3's NPPES data
will genuinely contain real provider names and NPIs that are FOIA-disclosable.

The scan is deliberately **not** part of `make check-all`. `check-all` is the fast inner-loop
command; scanning the whole corpus is a pre-publish gate you invoke on purpose, and burying a
slow check inside a fast one is how the fast one stops getting run.

Licensing markers are scoped to `data/raw/**` and `data/processed/**` rather than the whole
repo, because `data/README.md`, `docs/glossary.md`, and the scanner itself all discuss CPT, CDT
and the AMA by name. **Prose about the guardrail must not trip the guardrail** — the risk being
detected is a code table inside ingested data, not the word.

Baselines are whole-repo totals, so `--staged` / `--unstaged` / `--paths` runs list advisory
hits without comparing them and cannot exit 2. Comparing a whole-repo baseline against a subset
would report every marker as "below baseline" and advise lowering it, which is actively wrong.

Allowlist entries are principled classes, not enumerations, where a principle exists: `*@*.gov`
is a government contact point by definition, and a toll-free area code cannot be a personal
number — that one suppresses 1,429 helpline hits so the reviewable remainder is 39 numbers
rather than a haystack. The four non-government domains are listed individually on purpose, so
a *new* third-party domain surfaces as drift instead of being absorbed by a wildcard.

**Rejected:** a date-of-birth detector. In a corpus built on effective dates, transmittal dates
and revision histories it is pure noise, and would teach everyone to ignore the PII layer
wholesale. A DOB here would arrive attached to a name, which the e-mail/phone/NPI markers
already catch. Also rejected importing the licensing markers from `download_medicare_ncd.py`:
`scripts/` is not a package and that module pulls in `requests`/`bs4`, so they are duplicated
with a "change one, change the other" note — a dependency-free scanner was worth the copy.

**Dead end:** two regex shapes that had to be tightened rather than allowlisted, both now
pinned as anti-canaries. A bare `sk-[A-Za-z0-9_-]{20,}` matched inside the ordinary URL slug
`ask-about-preventive-services`; fixed by anchoring to real vendor prefixes plus a lookbehind.
`pii:npi` as "any Luhn-valid 10-digit run" reported a UUID fragment, a vendor PDF filename and
an IRS publink anchor — Luhn alone rejects only ~90% of arbitrary digit runs — so the
identifier must now also be *labelled* NPI. The general rule this establishes: allowlisting a
regex bug hides the next true positive that shares its shape.

The scanner then caught a live one during this session's own glossary edit: an illustrative
SSN written out in the `SSN` entry blocked the scan. Fixed by writing the *pattern* instead of
a specimen — the standing convention for documenting any blocking shape.

**Commits:** `699fdf3`

### 2026-07-31 — Medicare NCD corpus + glossary

**Did:** added the third and last Phase 0 bulk source — Medicare Coverage Database NCDs, 345
determinations — and [glossary.md](glossary.md), with a standing "keep it current" rule in
`CLAUDE.md` and a matching sweep step in `/wrap-up`.

**Decided:** fetch NCDs from the **MCD Coverage API**, not the bulk ZIPs `plan.md` points at.
The API's auth boundary falls exactly on this repo's licensing boundary — National coverage
endpoints answer keyless, LCD and Article endpoints `401` — so "NCDs only" stops being a rule
the script has to police and becomes the set of endpoints that answer at all. The fetcher
therefore never requests a license token, and `fetch_json()` treats `401` as fatal rather than
retryable so a mistake is loud.

The licensing scan **reports** `CPT`/`HCPCS` hits instead of failing on them. `medicare_pubs`
can claim zero occurrences; this corpus cannot — 22 records mention codes narratively in
revision histories. A scan that failed on those would have been switched off within a week, so
it distinguishes blocking markers (`©`, `CDT`, AMA/ADA notices — all zero) from advisory ones.

`effective_date` is `null` for the 75 longstanding NCDs, with CMS's explanatory sentence moved
to `effective_date_note`, rather than storing prose in a date field. `null` means "in force,
date unknown". `revision_history` is kept as a record field but excluded from `text` — it is
retrieval noise that would match date and number queries without answering them.

The glossary was scoped to *domain* vocabulary only, with each entry required to say what the
term means **in this repo** (licensing status, routing lane, schema field, correctness rule).
A bare dictionary expansion is not a useful entry, and the 256 vendored HealthCare.gov consumer
definitions are pointed at, not restated.

**Rejected:** the MCD Downloads-page ZIPs — 403 to non-browser clients, and their single
license click covers the AMA/ADA/AHA terms for Local coverage data sitting beside the National
data, which is precisely the conflation the guardrail exists to prevent. Also skipped the other
license-clean National document types the API serves (NCAs, CALs, MEDCAC materials, Technology
Assessments): they are the *process* behind a decision, and Phase 0 wants the rule.

**Stopped at:** clean. `plan.md` still describes the bulk ZIPs as the NCD route, which this
session superseded — see *Open questions*.

**Commits:** `58ed14a`, `d0b861c`

### 2026-07-31 — progress tracking

**Did:** added this file. Removed the `## Project status` section from `CLAUDE.md`, which now
carries only a pointer here. Added a `/wrap-up` command (`.claude/commands/wrap-up.md`) to
append log entries at the end of a working session.

**Decided:** status lives in exactly one place. `CLAUDE.md` is for conventions that must be
obeyed every session; status is read every session but obeyed never, and mixing the two
dilutes the guardrails and makes every status change look like a rules change in the diff.

**Rejected:** keeping a short status summary in `CLAUDE.md` alongside the full version here —
two sources of truth drift. Also rejected a `Stop` hook for auto-updating this file: it fires
on every pause, not on session end, so it would produce constant low-value entries.

### 2026-07-31 — frontend design

**Did:** wrote [frontend_plan.md](frontend_plan.md) (design only — no code). Added the frontend
slices to each phase in `plan.md` and the frontend section to `CLAUDE.md`.

**Decided:** FastAPI + React/Vite/TS/Tailwind/shadcn in a top-level `frontend/`. Vite proxies
`/api` to uvicorn in dev, so no CORS config anywhere. TS types are generated from the OpenAPI
schema rather than hand-written. Streaming is SSE over `fetch`, not `EventSource` — the latter
cannot send a POST body. The API contract is frozen now: `source_type`, `citations`, `claims`,
`trace`, and `abstained` as a first-class boolean, never inferred from answer text.

**Rejected:** a state-management library and a data-fetching library — there is no server state
worth caching yet. A corpus-browser page — citation drill-down covers most of the need.

**Left open:** frontend_plan.md §10 (multi-turn intent, eval runs over HTTP, corpus browser).

**Commits:** `87e2dc4`

### 2026-07-27 — Medicare publications corpus

**Did:** `scripts/download_medicare_pubs.py` → 83 publications / 964 pages into
`data/{raw,processed}/medicare_pubs/`. Guide in [medicare_pubs_data.md](medicare_pubs_data.md).

**Decided:** the second downloader mirrors the first one's shape rather than being generalized
into a framework — argparse CLI, `raw/` → `processed/` split, idempotent, `--normalize-only`
re-parse. Both emit `corpus.jsonl` with the same field vocabulary (`id`, `source`, `url`,
`title`, `bite`, `text`), which is what makes the sources interchangeable downstream.

**Commits:** `0bbc7b2`

### 2026-07-06 — HealthCare.gov corpus

**Did:** `scripts/download_healthcare_gov.py` → 803 articles/glossary/state pages. Guide in
[health_care_data.md](health_care_data.md). Revised the Phase 0/1 sections of `plan.md`.

**Decided:** Phase 1 splits into 1a (lexical retrieval, no vector DB) and 1b (embeddings behind
the same `retrieve` interface), so 1b has a baseline to be compared against.

**Commits:** `673e2ca`, `023c611`

### 2026-07-04 — tooling

**Did:** ruff, pyright, `Makefile`, and a pre-commit hook running both.

**Commits:** `c31edf5`, `d038216`, `97fb8f8`
