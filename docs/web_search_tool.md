# The web lane — Phase 2 design

The design document for [plan.md](plan.md)'s [Phase 2](plan.md#phase-2--add-the-web-search-tool),
the way [relational-tool.md](relational-tool.md) is Phase 1-c's. `plan.md` records *when* this
phase happens and what "done" means; everything below is *how*, and nothing here is repeated
there.

Read [agent.md](agent.md) first if you have not — this phase adds a third lane to the agent that
document describes, and it inherits the output schema, the provenance plumbing, the grounding
guardrail and the step limits unchanged. Nothing in the agent loop is rewritten.

**Status: built and measured (2026-08-20).** The lane ships and the numbers are in:
**routing 1.000 across three lanes**, `web_reach_rate` 1.000, and the third lane costs the first two
nothing measurable (recall@5 0.667 with it against 0.633 without, inside the noise band).
`make check-all` is green (373 tests, pyright clean) and `make smoke-web` passes 12/12 against live
Tavily. Full table, and the one regression — `abs-03` — in [agent.md](agent.md) §6. §17 below
records what building it changed about this design.

---

## 1. What this phase adds, and what it must not

**Adds:** one tool over Tavily — `web_search` — registered alongside the five reference-lane tools
and the three relational ones, and a third value of `source_type` that finally has something behind
it. Plus the eval questions that make three-lane routing measurable.

**Must not:**

- **Must not become a fourth corpus.** Nothing web-sourced is chunked, embedded, or written to
  `data/`. The web lane's evidence lives for exactly one agent run and then is gone. This is the
  same rule the mirrors have from the other direction — never chunked, never embedded — and it is
  what keeps `make chunk` / `make embed` / `make puf` the complete list of build steps.
- **Must not reshape the contract.** `SourceType` has carried `"web"` since Phase 0
  (`api/models.py`), `SourceBadge.tsx` already renders it amber, and `CitationCard.tsx` already
  handles a citation whose evidence is an external URL. This phase adds *values* to those fields.
  The one exception is an additive `web` field on `EvalRunSummary`, which is precedent —
  `toolset` landed at 1b and `structured` at 1-c for exactly the same reason. See §12.
- **Must not answer from the model's own knowledge under cover of a search.** The lane whose whole
  purpose is "things I do not hold" is also the lane where a model is most tempted to fill a gap
  from memory and attach a plausible URL. §6's guardrail extension is the answer, and it is code,
  not a sentence in the prompt.
- **Must not make an outage look like an answer.** A rate limit, a timeout, or a missing key
  produces an honest *"I could not check the web"*, never a fabricated answer and never a silent
  fallback to the corpus that pretends the web was consulted. §8.

### The one place this deviates from `plan.md`, recorded rather than buried

`plan.md` Phase 2 says the choice between Tavily and Exa is made *"by measuring both on the
out-of-corpus slice of the gold set rather than by reputation"*. **That measurement is not being
run. Tavily was chosen by decision.** Two reasons, and the second is the honest one:

1. §11 grades the web lane on **routing and groundedness, not answer correctness** — for reasons
   that are about the web's volatility, not about the vendor. A Tavily-vs-Exa A/B on that slice
   would compare two providers on "did the agent pick the web lane", which is a property of the
   prompt and the tool description, not of the search backend. It would measure almost nothing.
2. Resolving a difference between two competent search APIs needs more than the four or five web
   questions this gold set will carry, and `progress.md` already records that this set cannot
   resolve effects smaller than its own noise.

`plan.md`'s Phase 2 paragraph **has already been amended** to say the choice was taken rather than
measured, so the next reader does not go looking for a comparison that was never run. Done with this
document rather than deferred, because a plan that calls for a measurement nobody intends to run is
the kind of stale instruction that gets followed six months later.

---

## 2. What Tavily returns, and why the lane is shaped around it

Verified against [Tavily's `/search` reference](https://docs.tavily.com/documentation/api-reference/endpoint/search)
and `tavily-python==0.7.27`'s source, read at `AsyncTavilyClient._search`.

Request parameters this phase uses. Everything else the endpoint accepts is deliberately not
plumbed through; §15 says which and why.

| parameter | value | why |
|---|---|---|
| `query` | model-written | the tool's whole input |
| `search_depth` | `basic` | 1 credit; `advanced` is 2 and buys *broader source coverage*, not richer per-result text. Worth revisiting by measurement, not by assumption. |
| `topic` | `general` \| `news` | model-chosen — and it is the only way to get `published_date`, see below |
| `max_results` | config, default 5 | matches `retrieval.top_k` and `vectors.top_k` so a web result list is the same size as a passage list. Tavily's own guidance warns that raising it returns lower-quality results. |
| `chunks_per_source` | config, pinned at **3** (the maximum) | **the only lever on how much of each page comes back**, now that there is no widening tool. See below. |
| `time_range` | model-chosen, optional | `day`/`week`/`month`/`year` — the freshness control the lane exists for |
| `exclude_domains` | config, default `[]` | the only domain lever; §7 argues against an allowlist |
| `include_answer` | **false** | Tavily will synthesize an answer. Taking it would be handing the synthesis step to the search vendor, which is the pipeline this project is explicitly not building (`plan.md`: *"nothing chains retrieve → stuff-context → generate"*). It is also uncitable: it is Tavily's prose, not any source's. |
| `include_raw_content` | **false** | full page text on every one of five results would flood the context — and unlike a corpus chunk, nobody chose its size. §15 records what to do if truncated evidence turns out to bite. |
| `include_images`, `include_favicon` | **false** | nothing renders them |

**`chunks_per_source` carries more weight in this design than it looks like it does.** Confirmed
against Tavily's search best-practices guidance: it applies at both `basic` and `advanced` depth
(it is not `advanced`-only), and its range is 1–3 with a default of 3. Both depths return
query-reranked chunks rather than a page summary. Since this phase ships **no widening tool** (§3),
three chunks per source is the widest evidence a single call can return, so it is pinned at the
maximum and stated in config rather than left to the default — a value that matters should be
visible in a diff.

Each result carries:

```
title           str
url             str
content         str     # extracted, relevance-ranked page text — the citable evidence
score           float   # Tavily's relevance, 0..1
published_date  str     # ONLY when topic="news"
id              str     # Tavily's own, not stable across calls — we do not use it
```

**`content` is the reason `plan.md` insisted on an agent-oriented search API rather than a scraped
SERP**, and it is worth being precise about what it is: extracted page text, chunked and reranked
against the query, not a meta-description. That is what makes a web citation quotable at all — a
SERP snippet is a fragment written by a search engine, and quoting one verbatim would attach a
source's name to words the source may never have written.

**`published_date` only exists on `topic="news"`.** This matters more here than it would in a
general assistant. *"Is this current"* is the entire reason a health question goes to the web, and a
2019 article about a 2019 open-enrollment deadline reads exactly like a 2026 one. Consequences,
both in §6: `topic` is a model-visible argument so it can ask for news when currency matters, and a
web citation with no `published_date` renders as *"date unknown"* rather than as undated-and-fine.

Cost, from Tavily's published credit table: `basic` search is 1 credit, `advanced` 2. The free tier
is 1,000 credits/month. Rate limits are 100 RPM on a development key and 1,000 RPM on a production
key, with a `retry-after` header on 429. §8 is what does something with that header.

---

## 3. One tool

```
web_search(query, topic, time_range, max_results) -> WebSearchResults
```

Registered **alongside** everything from 1a, 1b and 1-c — never instead of. The whole measurement of
this phase is whether the agent picks the right *kind* of source when three kinds exist, which it
cannot do if it only has one.

### Why one, when the reference lane has five

Because this phase is about **routing between lanes, not about depth within one**, and the crudest
thing that could work is the right starting point — the same judgement Phase 1a made when it stood
up the whole agent on stdlib BM25 rather than a vector store. Tavily's `content` is already
query-reranked extracted page text with the source URL attached, which is precisely what `plan.md`
demanded when it ruled out a scraped SERP. One call produces citable evidence. Nothing else is
required to answer a web question.

Every additional tool is also additional surface for the agent to route badly *within* a lane, at a
phase whose entire metric is whether it routed correctly *between* them. Keeping the web lane to one
tool means a Phase 2 routing number is not partly a measurement of how well the model navigates a
two-tool web lane.

### What is knowingly given up

The reference lane has `get_chunk` for a reason: **a hit that lands mid-sentence is a different
failure from a hit that is wrong, and it has a different fix.** That failure exists here too —
Tavily returns up to three reranked chunks of a page, and a deadline stated in the sentence after
the one that matched falls outside them exactly as it falls outside a 1,200-character corpus chunk.

With no widening tool, the agent's only recovery is **to search again with a narrower query** — a
second credit, a fresh ranking, and a real chance of the same snippet. Two things compensate, and
neither fully:

- `chunks_per_source` is pinned at its maximum of 3 (§2), so each result is as wide as one call can
  make it.
- The tool description tells the model that its recovery move is reformulation, not re-reading —
  because a model that has learned `get_chunk` exists in one lane will otherwise reach for its
  absent equivalent here and burn a retry discovering it is not there.

§15 records `read_url` over Tavily's `/extract` as the deferred fix, with the condition that should
trigger building it: **truncated web evidence showing up as a real failure in the trace**, not as an
anticipated one. Build it when it bites.

### Tool description is prompt surface

Same rule as `tools.py` and `structured_tools.py`: the docstring is text the model reads, so it is
written for that reader and names its siblings. Four things this tool's description must carry that
no other lane's does:

1. **When *not* to come here.** *"What is a deductible"* has an answer in the corpus; a web search
   for it returns an insurer's marketing page that is cited, plausible, and worse than the
   HealthCare.gov glossary entry. The description says so.
2. **That the open web is not authoritative.** The reference corpus and the mirrors are CMS
   publications. A web result is whatever ranked. The description tells the model to prefer an
   official source *among the results it got*, and to say whose page a claim came from.
3. **That an undated result is a weak result for a currency question.** See §2 on
   `published_date`.
4. **That there is no way to read further into a page.** The recovery move is a narrower query, not
   a request for more of the same result — see *What is knowingly given up* above.

---

## 4. Client, and why the SDK

**`tavily-python`, driven through `AsyncTavilyClient`.** Decided. The alternatives and what each
would have cost:

| option | verdict |
|---|---|
| **`tavily-python` SDK** | **Chosen.** Typed exceptions for every failure mode §8 has to distinguish (`InvalidAPIKeyError` 401, `UsageLimitExceededError` 429, `ForbiddenError` 403/432/433, `BadRequestError` 400, `TimeoutError`), and — the load-bearing part — its constructor accepts `client: httpx.AsyncClient`, which is what makes §13's offline test suite possible. |
| direct `httpx` POST | Rejected. Would avoid the `tiktoken` transitive dependency (a compiled wheel the SDK only needs for `get_search_context`, which we never call), but we would hand-write and hand-maintain the error mapping the SDK already ships. |
| `pydantic_ai.common_tools.tavily.tavily_search_tool` | **Rejected, and the reason is structural rather than aesthetic.** Read the source at `pydantic_ai/common_tools/tavily.py`: it returns a `TypedDict` of `title/url/content/score` only, records nothing in `deps.trace`, and registers nothing in the seen-evidence map. Under this repo's guardrail every web citation it produced would be rejected by `_validate_grounding`, because no tool told the validator the result existed. It is a fine tool for an agent with no provenance requirement; this is not one. |

Two consequences of the SDK choice worth writing down before someone "tidies" them:

**`search()` returns an untyped `dict`, so we validate it ourselves.** `web/models.py` holds
Pydantic models for the response, and the boundary is where a missing `content` or a non-float
`score` becomes a clear error instead of an `AttributeError` three frames later. This is the same
argument `plan.md` makes for Phase 3's typed API wrappers, arriving one phase early because the
first live third-party response arrives here.

**The SDK does not retry, and ignores `retry-after`.** `_search` posts once and maps the status
code to an exception. So the 429 handling in §8 is ours to write; it is not inherited.

---

## 5. Bounding a lane that costs money

Every prior lane is free and local. BM25 is 3 ms of CPU, LanceDB is a flat scan, DuckDB reads
Parquet off the disk. **The web lane is the first tool in this repo where a single agent run can
spend real money and real seconds**, and the ceilings that follow are the analogue of
`relational-tool.md` §4c's row cap and timeout: they do not make a bad call impossible, they make
it cheap.

Three bounds, none of which the model can see or forget to apply:

- **Per-run search budget** (`web.max_searches_per_run`, default 3). Counted on `AnswerDeps`
  exactly as `queries` counts structured statements. Past the budget the tool returns the §8
  refusal shape rather than raising — the model is told it is out of searches and should answer
  from what it has or abstain, which is a recoverable state, not an error.

  **This budget is doing more work than it would with a widening tool.** §3 makes reformulation the
  only recovery from a truncated snippet, so a run that keeps not-quite-finding an answer spends its
  whole allowance on near-identical searches. Three is a starting value chosen to leave room for one
  reformulation plus one topic change; it is the first number this phase should revisit against real
  traces.
- **Request timeout** (`web.timeout_s`, default 20). The SDK's default is 60 and its cap is 120. A
  browser waiting on an SSE stream does not have two minutes, and `agent.request_limit` gives a run
  twelve model calls — one tool call must not be able to eat the whole request.
- **`agent.tool_calls_limit`**, unchanged, already caps the whole loop. The per-lane budgets sit
  *inside* it and exist because a run that spends all 16 tool calls on web searches is a different
  pathology from one that spends them on `describe_table`.

**These are ceilings, not tunables** — the same distinction `config.py`'s `StructuredConfig`
docstring draws. Nothing here makes an answer better. `search_depth` and `max_results` *are*
tunables and live in the same config block, which is fine; what matters is that neither can be
raised from an environment variable (see §10).

---

## 6. Provenance: what a citable web result is

This is the section that decides whether the phase is trustworthy, and it is a direct extension of
[agent.md](agent.md) §4 rather than a new idea.

### The evidence unit

```python
class WebResult(BaseModel):
    result_id: str          # "web#s1.2" — search 1, rank 2
    url: str
    domain: str             # registrable domain, shown to the model and in the citation
    title: str
    content: str            # Tavily's extracted text. The ONLY quotable surface.
    score: float | None
    published_date: str | None   # only ever set when topic="news"
```

`result_id` is modelled on `relational-tool.md` §6's row id and for the same reason: **a citation
must name something the run actually produced, not something the model can construct.** A URL fails
that test — a model can write a plausible URL it never retrieved, and `https://www.cms.gov/…` looks
exactly as citable whether a tool returned it or not. `web#s1.2` cannot be guessed into existence,
because nothing has that shape until a search assigns it.

### The guardrail extension

`AnswerDeps` grows `seen_results: dict[str, WebResult]`, alongside `seen_chunks` and `seen_rows`.
`tools` register every result they return. `_validate_grounding` grows a third branch:

- **Unknown `result_id`** → `ModelRetry`, listing the ids this run actually has. Identical in shape
  to the unseen-chunk and unseen-row messages.
- **Snippet not present in that result's `content`** → `ModelRetry`. Compared with the
  **whitespace-normalized** comparison `_normalize` already applies to chunks, *not* the byte-exact
  one `_validate_row_citation` applies to cells. Extracted web text wraps and re-wraps arbitrarily;
  a byte-exact test would reject genuinely verbatim quotations and send the model into a retry loop
  it cannot win. A table cell has no wrapping to survive and its whitespace is data, which is why
  that lane is stricter. The two rules disagree on purpose, and each is right about its own
  evidence.

### `AgentCitation` grows a third shape — and the shape check has to be restructured

`agent/models.py`'s `AgentCitation` is one class carrying two shapes today (`chunk_id + snippet`,
`row_id + cells`), discriminated by `_check_shape` rather than by a union — the reasoning is in its
docstring and still holds: a discriminated union means an `anyOf` in the output schema, which
strict-JSON modes handle unevenly, and the model must be able to mix lanes in one list.

Adding `result_id + snippet` **breaks `_check_shape` as written**, and this is the kind of thing
that is much cheaper to notice now than in a retry loop. The current check detects a passage as
`chunk_id is not None or snippet is not None` — but a web citation also carries a `snippet`, so
every web citation would be misread as a malformed passage. The fix is to discriminate on the **id
field alone** (exactly one of `chunk_id` / `row_id` / `result_id` set) and then require that id's
companion field. Same class, same flat schema, one more branch.

### The wire citation

Built in `runtime._web_citation`, from the `WebResult` and never from the model — the rule that has
held since Phase 1a. The model contributes *which* result and *which words*, both already
validated; everything displayed is read off the result:

```python
Citation(
    id=citation.id,
    source_type="web",          # the enum value that has existed since Phase 0
    title=f"{result.title} — {result.domain}",
    url=result.url,
    doc_id=None,                # no corpus document behind it
    chunk_id=None,              # nothing to drill into; the url is the drill-down
    snippet=citation.snippet,   # verbatim, checked above
    score=None,
)
```

The domain is in the title rather than left implicit in the URL because the citation card shows the
title and hides the URL behind a link. For a health question, *who is saying this* is part of the
claim, and §7 argues that surfacing the source is the alternative to filtering it.

### A frontend bug this creates, found by reading rather than by running

`frontend/src/components/CitationCard.tsx` decides whether to render a citation as table cells with:

```ts
function isRow(citation: Citation): boolean {
  return citation.chunk_id == null && citation.doc_id == null && citation.snippet.includes(': ')
}
```

A **web citation satisfies the first two conditions**, and its snippet is arbitrary prose that may
well contain `": "` — *"The deadline is: January 15"*. It would render as a `<dl>` of fake columns.
The component's docstring says it keys off the absence of a chunk deliberately, so that Phase 3's
live-API rows keep working — that reasoning was right for the citation shapes that existed when it
was written and is wrong the moment a third one appears. Fix: require `source_type ===
'structured_api'` as well. It is a two-line change and it belongs in this phase's build order (§14,
step 8), not in a later bug report.

---

## 7. Result hygiene, and the allowlist that is deliberately absent

`plan.md` asks for *"basic web-result hygiene (dedupe, source filtering)"*. What that means here:

**Dedupe by URL**, after normalizing away `#fragment` and tracking query parameters. Tavily can and
does return the same page reached two ways.

**Cap results per domain** (`web.max_results_per_domain`, default 2). Without it, one site with
good SEO fills the result list and the agent's "several sources agree" is one source repeated.

**Surface the domain** on every result the model sees and every citation the reader sees (§6).

**No allowlist.** Restricting `include_domains` to `cms.gov`, `medicare.gov`, `healthcare.gov` and
`fda.gov` sounds like the safe choice for a health tool and is the wrong one, for a reason specific
to what this lane is *for*: the questions that reach it are exactly the ones no official page has
published yet — *"did anything change for the 2026 plan year"*, *"was there a recall this week"*,
*"did this insurer have a market-conduct action"*. An allowlist turns the web lane into a slower,
worse copy of the reference lane and guarantees an abstention on the questions the phase exists to
answer. It also hides the failure it appears to prevent: an official page that does not address the
question still ranks first within an allowlist, and a citation from `cms.gov` that does not say what
the answer says is *more* dangerous than a clearly-labelled one from a news site.

The alternative is the one taken: search the open web, show the reader whose page it is, and let the
prompt tell the model to prefer an official source *among what it found*. `web.exclude_domains`
stays in config as the escape hatch for a site that proves to be noise — empty by default, and a
value there is a reviewable line in a committed diff.

**Not doing:** an `authoritative: bool` flag on results, or any re-ranking of Tavily's order. Both
are heuristics that would need their own justification and their own measurement, and this phase's
eval slice grades routing, not result quality — so neither could be defended with a number. §15.

---

## 8. When the web is not there

`plan.md`'s requirement: *"the tool is wrapped so a rate-limit or an outage degrades to an honest
'couldn't check the web' rather than a fabricated answer."* Three distinct situations, and
conflating them is how a missing key gets reported as "no results found".

### The key is missing

`TAVILY_API_KEY` absent or blank. Handled at the same layer as a missing vector store or a missing
Parquet mirror — **before the agent runs**, not inside a tool:

- `AppContext.web` is `None`, exactly as `vectors` and `structured` can be.
- `routes/chat.py`'s `_unavailable` gains a fourth branch: `agent.web_tools` on with no client → a
  503 naming the fix (*"set `TAVILY_API_KEY` in `.env`, or set `agent.web_tools: false`"*), in the
  same family as `NO_CORPUS` / `NO_VECTORS` / `NO_PLAN_DATA`, and for the same reason those refuse
  to degrade: an answer produced by a narrower set of lanes than the operator configured is a real
  answer measured against a setup nobody chose, with nothing in the response to say so.
- `/api/health` reports the `web` lane as `configured: false` with the reason — that endpoint's
  `web` entry currently hardcodes *"Phase 2 — no web-search tool yet"* and this phase replaces it.

**`Secrets.tavily_api_key` is optional, unlike `openai_api_key`.** A clone that only wants the
reference lane must still boot. But the blank-string trap `settings.py` documents at length applies
here *more* strongly: `.env.example` already ships `TAVILY_API_KEY=` empty, so a `cp .env.example
.env` produces `""` — a perfectly valid `str` that would boot cleanly and fail with a 401 on the
first web question. So the field is `SecretStr | None = None` **with a validator mapping `""` to
`None`**, and "no key" and "empty key" become the same, well-handled state rather than two states
one of which is a live 401.

### Tavily fails mid-run

429 (`UsageLimitExceededError`), 403/432/433 (`ForbiddenError` — plan or PayGo limit), a timeout,
or a 5xx. The tool does **not** raise, and does not `ModelRetry`:

```python
class WebSearchResults(BaseModel):
    results: list[WebResult]
    unavailable: str | None = None   # why the web could not be checked, in the model's words
```

`unavailable` set means `results` is empty *for a reason that is not "nothing matched"*. The
tool description and the prompt both spell out what to do with it: **say you could not check the
web, answer from the corpus only if it genuinely covers the question, and never fill the gap from
memory.** That is a first-class outcome, not an error.

Three rejected alternatives, each of which loses something:

- **Raise, and let it surface as an `ErrorEvent`.** Kills the whole answer over one unavailable
  lane, including the part the corpus could have answered.
- **`ModelRetry`.** Invites the model to retry something that will not recover inside a run, and
  spends the grounding guardrail's budget on a network condition.
- **Return an empty list.** The worst option, and worth naming because it is the laziest: empty
  results and an unreachable API become indistinguishable, and `relational-tool.md` §6 already
  argued the general form of this — *"zero rows is a finding, not a failure"*. Zero web results
  means the web does not appear to cover this. An outage means nothing was asked. A health tool
  must not report the second as the first.

**One retry, on 429 only, honouring `retry-after`**, and only when the header asks for less than
`web.retry_after_cap_s` (default 5). Beyond that it degrades immediately rather than holding an SSE
stream open. This is ours to write because the SDK does not do it (§4). It is the transport-level
analogue of `agent.request_retries` and is deliberately much smaller: a paid eval run of 40
questions can afford to wait out an OpenAI TPM limit, and a user watching a spinner cannot.

Anything more — a response cache, backoff across requests, a token bucket — is **Phase 3's**, which
`plan.md` explicitly scopes as *"rate-limit handling, retries, and a response cache"*. §15.

---

## 9. Module layout

Mirrors `structured/` exactly, and for the same reasons `relational-tool.md` §9 gives:

```
src/health_coverage_navigator/
  web/
    __init__.py
    models.py          # WebResult, WebSearchResults, ReadResult — prompt surface
    client.py          # WebSearchClient over AsyncTavilyClient; hygiene, budgets, degradation
  agent/
    web_tools.py       # web_search: the trace, and seen_results
```

Three rules this layout enforces:

- **`web/` imports nothing from `agent/`.** Same as `structured/`. It is what lets every client test
  run with no model, no agent, and no PydanticAI import.
- **`agent/web_tools.py` is a third tool module, not more of `tools.py`.** Each lane's docstrings
  are prompt surface, and keeping them in separate files is what has stopped the reference lane's
  guidance from drifting into the relational lane's. A third lane in `tools.py` would put three
  lanes' worth of model-facing prose in one file.
- **`select_tools` stays the single place that decides what a run can do.** `tools.py` composes
  `WEB_TOOLS` in, exactly as it already composes `STRUCTURED_TOOLS`.

`web/models.py` holds the model-facing shapes rather than `agent/models.py`, for the same structural
reason `structured/models.py` exists: `client.py` builds them and must not import from `agent/`.

---

## 10. Configuration, and the one secret

The split is `configuration.md`'s and this phase is a clean instance of it: **the key is the only
thing that goes in `.env`, and every value that could change an eval score goes in `config.yaml`.**

`settings.py` — one field, optional, blank-is-none (§8):

```python
tavily_api_key: SecretStr | None = Field(
    default=None,
    description="Tavily API key. Server-side only — never expose via a VITE_* var.",
)
```

`config.yaml` — a new `web:` block:

```yaml
web:
  search_depth: basic          # 1 credit; advanced is 2 and buys breadth, not richer results
  max_results: 5               # matches retrieval.top_k / vectors.top_k
  chunks_per_source: 3         # the maximum, and the only width lever there is (§2, §3)
  max_results_per_domain: 2    # hygiene (§7)
  max_searches_per_run: 3      # budget (§5) — the first number to revisit against real traces
  timeout_s: 20.0              # SDK default is 60; a browser is waiting
  retry_after_cap_s: 5.0       # honour a short 429 retry-after, degrade past it (§8)
  exclude_domains: []          # empty by design (§7); a value here is a reviewable diff line
```

and `agent.web_tools: true` beside `agent.structured_tools`.

**`search_depth` and `max_results` are exactly why this block cannot be environment-readable.**
Both change what the agent sees and therefore what it answers; a run configured by an unrecorded
`WEB_MAX_RESULTS=10` could not be reproduced from the repo. `config.py`'s docstring makes this
argument in general and CLAUDE.md makes it as a rule; this is the instance where it would be most
tempting to break, because the key next to it *does* come from the environment.

`Config.fingerprint()` covers the new block automatically, so an eval run record already pins these
values. Nothing to add there.

---

## 11. The third axis

`agent.md` §3 describes what a run can see as a per-run choice **on two independent axes**. This
phase makes it three: `toolset` (how the reference lane is searched), `structured` (whether the
relational lane exists), `web` (whether the web lane exists).

```python
select_tools(toolset, structured=False, web=False)
system_prompt(toolset, structured=False, web=False)
_build_agent(model, toolset, structured, web)     # lru_cache key
```

A third boolean rather than a fourth `Toolset` value, for the reason already settled at 1-c: these
select *lanes*, and folding them into the toolset name would make twelve combinations and would
redefine the three names Phase 1b's measurement is recorded under.

### The cache-key trap, for the third time

`agent.md` §3 records this bug twice already. `_build_agent` is `lru_cache`d, and **every default
must be resolved in `build_agent` before the lookup**, because `build_agent()` and
`build_agent(None)` are different cache keys — which at Phase 1a meant a test's `agent.override(...)`
applied to an object nobody used and the run went to the real provider. Phase 1-c added a third
defaulted argument and took the same bug again. **This phase adds a fourth. Resolve `web` in
`build_agent`, and derive the key in `conftest.py`'s `AgentKit._agent` the same way `stream_answer`
does.** Bump `_build_agent`'s `maxsize` from 24 to 48 while you are there — 3 toolsets × 2 × 2 × a
few models — so an eval sweep does not evict the agent it is about to reuse.

### The prompt composes with all three

Same rule, third instance: **describing a tool the agent does not have makes an eval partly a
measurement of how well a configuration copes with misleading instructions.** Two fragments change:

- `_SOURCES_*` — every existing variant ends with *"no web search"*, which becomes false. The
  reference-only and structured-only variants keep it; two new variants drop it and describe the
  web lane instead.
- `_OUT_OF_REACH_*` — every variant currently lists *"anything needing current news"* as a reason
  to abstain. With the web lane registered that is the wrong instruction and it would cause exactly
  the expensive failure: abstaining on the questions this phase exists to answer.

Rather than write four hand-maintained combinations, compose the *sources* and *out-of-reach*
fragments from per-lane pieces. `tests/test_agent.py` already asserts that no prompt names a tool
its toolset does not register; extend it to the new axis and it will catch a stale combination.

Also worth stating in the prompt, because no other lane needs it: **the corpus and the mirrors are
CMS publications and the web is whatever ranked.** When both answer, prefer the one you hold.

---

## 12. Evals: what three-lane routing actually measures

### The metric widens without changing

`evals/grading.py`'s `routing_grader` scores the **majority lane of the citations** against
`expected_source_type`. Read it again with a web answer in mind: it counts `Citation.source_type`
values, and `"web"` is one. **It needs no change at all.** Its own docstring predicted this —
*"Phase 2 widens this to three lanes by adding questions, not by changing this function"* — and
that prediction holds. This is the payoff of having built the routing metric at 1-c.

### Grading routing and groundedness, not answer correctness — and why

**Web gold questions carry an expected lane and no expected answer.** They are graded on:

- `routing_correct` — did the answer rest on web citations? (the new three-lane measurement)
- `groundedness` / `citation_resolution` — is every quoted snippet verbatim in what Tavily returned?
- `false_abstention_rate` — did it decline a question the web can answer?

and **not** on `answer_key_facts` or an `expected_answer`.

The reason is that a gold answer for *"was there a recall this week"* is wrong within a week of
being written, and a gold set that rots silently is worse than one that admits its scope: the
number keeps being reported, keeps dropping, and the drop means nothing about the agent. The three
metrics above are all stable properties of the *system* rather than of the world — routing is about
which tool it chose, groundedness is about whether it quoted honestly, and neither moves because a
news cycle did.

The cost, stated plainly: **this phase does not measure whether web answers are any good.** It
measures that the agent goes to the web when it should, and does not invent what it found there.
That is the honest boundary of what a static gold set can assert about a live source, and pretending
otherwise would put a rotting number on the dashboard.

### Gold-set changes

**`abs-04` converts from an abstention to a web question.** It already carries
`becomes_answerable_at_phase: 2` and its note already says *"answerable once web search is wired
up"* — the gold set anticipated this exactly. That conversion is itself a small proof the phase
boundary was drawn in the right place.

**`abs-03` stays an abstention, and gets harder.** *"What will the standard Part B premium be in
2027?"* is a figure CMS has not published. With web search registered, the agent can now find
speculation, projections, and advocacy-group estimates about it — so this question stops testing
*"do you notice the corpus lacks this"* and starts testing *"do you notice that finding something
written about a figure is not the same as the figure being published."* That is a more valuable
question than it was, and it must not be quietly converted.

**Four or five new `web-*` questions**, spanning the ways the lane can be reached: a currency
question (a recent recall, a deadline for the current cycle), an out-of-corpus-entirely question,
and — most valuable — at least one **trap**: a question the *corpus* answers well but whose phrasing
invites a web search (*"what's the latest on how deductibles work?"*). Routing is measured by the
lane it should *not* have chosen as much as by the one it should.

### Schema and scoring changes, concretely

Reading the code, three things break on a web question and each needs a named fix:

1. **`GoldQuestion._check_shape` rejects it.** A non-abstain, non-structured question is required to
   carry `corpus`, `expected_doc_ids`, `expected_snippet`, `expected_answer` and `answer_key_facts`
   — a web question has none of them. Needs a **third shape**, keyed off
   `expected_source_type == "web"` (an `is_web` property beside `is_structured`, derived from the
   lane rather than a separate flag, for the same reason `is_structured` is).

2. **`aggregate()` would put it in the recall denominator and score a guaranteed miss.** The
   current split is `not expected_abstain and expected_source_type != "structured_api"` — an
   exclusion list that has to grow with every lane. Invert it to
   `expected_source_type == "reference"`, which is what it always meant and which no future lane can
   silently break. **This is the same failure `relational-tool.md` warned about at 1-c: a headline
   metric moving because the question set changed is exactly how a phase that adds a capability
   looks like a regression.**

3. **`score_question` has no branch for it.** A web question passes when it did not abstain and its
   citations are majority-`web`. The routing grader reports the metric; `passed` should agree with
   it rather than being decided by a retrieval rank that does not exist.

### Runner and record

- `--web` / `--no-web` on `evals/runner.py`, beside `--structured`, defaulting to
  `agent.web_tools`. Agent runner only — same guard as `--structured`.
- `EvalRunSummary.web: bool | None`, additive, `None` for every runner with no agent in it. A
  comparison between two runs that differ only in this is only possible if a run says which it was —
  the argument `toolset` and `structured` already carry. Needs `make types`.
- **`make eval-web`**: the agent with the web lane on, over the gold set.
- **`make eval-no-web`**: the same, with it off. **The comparison that matters this phase** — does
  the third lane cost anything on the 30 reference questions and 4 structured ones that were already
  answered? 1-c ran exactly this comparison for the second lane and found 0.800 against 0.767,
  inside the noise band. Run it again for the third, and read it against `progress.md`'s standing
  caveat that a single agent run at fixed config has a 0.200 spread.

### The known-unclosed hole this phase makes worse

`progress.md` records that **eval run records carry no tool trace**, so no question about tool
*choice* can be answered from a run file — and it already names Phase 2's routing slice as the thing
that needs it. That assessment is right and this design does not close it. `routing_grader` scores
citations, which is deliberate (a lookup the agent ran and then ignored is not evidence), but *"how
often did it search the web and then answer from the corpus anyway"* is a genuinely interesting
Phase 2 question that no run file can answer. A `tools_used: list[str]` on `EvalQuestionResult`
would be additive and cheap. **Recommended in this phase, and small enough to be step 7 of §14.**

---

## 13. Tests — all offline, all in `make check-all`

`tests/conftest.py` carries one invariant in bold: **no test may reach a provider.** It is enforced
twice today — `ALLOW_MODEL_REQUESTS = False` for PydanticAI, and an autouse fixture that replaces
`openai_embedder` because an embedding call bypasses that flag entirely. **Tavily is a third bypass
and needs a third guard**, built the same way and for the same reason: without it, a test that
accidentally used the live client would *pass*, quietly, spending the developer's credits.

```python
@pytest.fixture(autouse=True)
def _no_live_web_search(monkeypatch):
    # Patched at the factory, not at the SDK, so the failure names the seam
    # a test should have used instead of surfacing from inside httpx.
```

Same "call it through the module" rule `embedder.openai_embedder` documents: a name bound at import
time in a consumer module would not be replaced.

**The client itself is tested against `httpx.MockTransport`.** This is the concrete payoff of the
SDK choice in §4: `AsyncTavilyClient(api_key=..., client=httpx.AsyncClient(transport=...))` lets the
whole wrapper — hygiene, dedupe, per-domain cap, budgets, every §8 degradation path, the 429
`retry-after` — run against canned HTTP responses with no network and no key.

**Fixtures are synthetic, not recorded — and this is a licensing decision, not a testing preference.**
It would be natural to record real Tavily responses to committed JSON. **Do not.** A Tavily response
body contains extracted text from third-party web pages, and this repo is public. CLAUDE.md's data
guardrail requires per-source clearance before anything is committed, and a news article's body text
is copyrighted by its publisher — the guardrail exists precisely to stop content whose licence
nobody checked from being vendored. Hand-written fixtures over `example.org` / `example.com` URLs
with invented content satisfy every test above, are clearer to read in a diff, and raise no question
at all. Run `make scan` over them like anything else.

Test list:

| what | asserts |
|---|---|
| `tests/test_web_client.py` | hygiene (dedupe, per-domain cap), budgets, every §8 failure mapped to `unavailable`, the 429 retry honouring and capping `retry-after`, response validation rejecting a malformed body |
| `tests/test_agent.py` (extended) | the guardrail's third branch — an unseen `result_id` and a non-verbatim web snippet each rejected, **with the retry message asserted**, because a retry the model cannot act on is a retry wasted; the four-axis prompt naming no tool it does not register |
| `tests/test_settings.py` (extended) | a blank `TAVILY_API_KEY` reads as `None`, not `""` |
| `tests/test_evals.py` (extended) | a `web` gold question validates, stays out of the recall denominator, and is scored on routing |
| `tests/test_api.py` (extended) | the 503 when `web_tools` is on with no key; `/api/health` reporting the lane |
| frontend | `CitationCard` renders a web citation as prose, not as row cells (§6's bug) |

**`make smoke-web`**, outside `check-all`, beside `make smoke` and `make smoke-abstain`: one live
Tavily call and one live model call on an out-of-corpus question, asserting the agent chose the web
lane and cited a URL that a tool actually returned. Same three-rung logic `scripts/smoke.py`
documents — a failing test means this repo is wrong, a failing smoke check might mean Tavily changed
— and folding them into one command teaches you to shrug at red.

---

## 14. Build order

Ordered so each step is verifiable before the next depends on it, and so nothing costs a credit
until the offline half is proven.

1. **`settings.py`** — optional `tavily_api_key`, blank-is-none, plus its test. Nothing else
   changes. (Verifiable with `make check-all` alone.)
2. **`web/models.py` + `web/client.py`** — the typed response models, hygiene, budgets, and every
   §8 degradation. `tests/test_web_client.py` against `MockTransport`. **No agent involvement yet**,
   so a failure here is unambiguously the client's.
3. **`config.yaml` + `config.py`** — the `web:` block and `agent.web_tools`.
4. **`agent/web_tools.py`** — the tool, the trace, `seen_results`. Not yet registered.
5. **The guardrail** — `AgentCitation`'s third shape and the restructured `_check_shape` (§6),
   `_validate_grounding`'s third branch, `runtime._web_citation`. Tests assert the retry messages.
6. **Registration** — `select_tools`'s third axis, the composed prompt, `_build_agent`'s fourth
   cache key and the `conftest` key that must match it (§11 — the bug this repo has taken twice).
   `AppContext.web`, the `_unavailable` branch, the `/api/health` lane entry.
7. **Evals** — `GoldQuestion`'s third shape, `aggregate`'s inverted split, `score_question`'s
   branch, `--web` / `--no-web`, `EvalRunSummary.web`, `make types`. Plus `tools_used` on
   `EvalQuestionResult` (§12) while the file is open.
8. **Frontend** — the `CitationCard.isRow` fix (§6), and `make types` propagated via the
   `sync-frontend` skill. **Then open it in a browser.** `progress.md` records that 1b's
   run-comparison view and 1-c's row card both shipped without a human ever looking at them; this
   phase should not make it three.
9. **Gold set + docs** — convert `abs-04`, add the `web-*` questions, and add any domain term the
   new questions drag in to [glossary.md](glossary.md) *in the same change* — the standing rule.
   (`plan.md`'s Tavily-vs-Exa paragraph is already amended; see §1.)
   `glossary.md` already anticipates this lane: its **open enrollment** entry calls itself *"the
   repo's canonical `web`-lane eval question"*.
10. **`scripts/scan_sensitive.py`** — add `tvly-` to the `cred:vendor-secret-key` marker's
    vendor-anchored prefix list, with a canary, and run `make scan-selftest`. Today a leaked Tavily
    key is only caught if it happens to sit beside an `api_key =`; the vendor prefix catches it
    anywhere. Cheap, and this repo is public.
11. **Measure** — `make eval-web` and `make eval-no-web`, then write the numbers into
    [agent.md](agent.md) §6 as a new subsection, the way 1-c's and 1b's are recorded. `progress.md`
    gets the state; `agent.md` gets the measurement.

Steps 1–8 are free and offline. Only 11 spends money.

---

## 15. Deferred deliberately

Each of these is a real capability, left out with a reason rather than forgotten.
**Active development ended after Phase 3; `read_url` and its trigger condition are carried forward
in [future_enhancements.md](future_enhancements.md) §3, which indexes this section rather than
replacing it.**

- **`read_url` over Tavily's `/extract`** — the `get_chunk` of this lane, and the most likely thing
  to be needed next. §3 states what its absence costs: a snippet that stops one sentence short can
  only be recovered by searching again. Deferred because Phase 2's measurement is lane routing, and
  a two-tool web lane would make that number partly about navigation within the lane.

  **The condition that should trigger building it**, so this stays a decision rather than an
  omission: traces showing repeated near-identical searches against the same domain, or web answers
  that abstain while a cited result plainly contains the topic. Both are visible in the trace panel
  and neither needs new instrumentation. Build it then, not on the anticipation.

  Design, already worked out so it does not need re-deriving: post to `/extract` with the
  **originating query** and `chunks_per_source`, never a bare URL — bare `/extract` returns
  `raw_content` for the whole page, which for a CMS regulation page is tens of thousands of
  characters, and dropping that into a 12-request run is how a run becomes a rate-limit incident.
  With `query` set it returns the top-ranked chunks of that page, which is what makes it a widening
  move rather than a document dump. Cost is 1 credit per 5 successful URLs at `basic` depth. It
  would need its own per-run budget (`max_reads_per_run`) for the reason §5 gives — three searches
  is a model reformulating, three reads is a model trying to read the internet.

  **Try the cheap fix first.** `include_raw_content` on `/search` costs no extra credits and returns
  full page text for every result; it was rejected in §2 for flooding the context, not for cost. If
  truncation bites, measure whether raising `max_results` down to 3 with raw content beats 5 results
  without it before adding a second tool. That is a config change and a run, against a module and an
  eval axis.
- **A URL-fetching tool of our own** — `httpx.get` plus an HTML parser, rather than `/extract`.
  Rejected outright rather than deferred. Owning an extraction pipeline (boilerplate stripping,
  encoding, JS-rendered pages, robots) is exactly what `plan.md` rejected when it ruled out a
  scraped SERP, it has nothing to do with tool routing, and it would make a citation's evidence
  depend on which tool happened to find the page.
- **A response cache.** `plan.md` scopes *"rate-limit handling, retries, and a response cache"* to
  Phase 3, and it belongs with the Marketplace API and openFDA where per-record lookups repeat. A
  cache would cut the cost of repeated eval runs, which is the one honest argument for pulling it
  forward — and it is not enough, because a cached web result silently makes a *freshness* lane
  stale, which is the one property this lane exists to have.
- **Anything beyond one 429 retry.** Backoff, a token bucket, cross-request pacing: Phase 3. §8's
  single capped retry is the minimum that stops a transient 429 from reading as an outage.
- **An Exa comparison.** §1 records why the measurement `plan.md` called for is not being run, and
  that `plan.md` should be amended rather than left implying it was.
- **`include_answer`.** Tavily will synthesize. Taking it hands the synthesis step to the search
  vendor — the pipeline this project exists not to build — and produces prose no source said, which
  is uncitable under §6 by construction.
- **An `authoritative` flag or any re-ranking of Tavily's order.** §7. Both are heuristics that this
  phase's eval slice — routing and groundedness — could not defend with a number.
- **`/crawl` and `/map`.** Whole-site traversal answers no question in this domain that a search
  plus a targeted extraction does not, and it is the fastest way to spend a month's credits by
  accident.
- **Multi-hop web reasoning** — searching, reading, and searching again on what was read. That is
  Phase 4's planning-and-decomposition work, over the tool this phase registers.
- **Web results in the `plan_year` rule.** `plan_year` selects a Parquet partition (`relational-tool.md`
  §7); the web has no partitions. A web answer about the wrong plan year is a *content* problem the
  prompt addresses, not a lane the request can pin. Worth revisiting if it turns out to be a real
  failure mode — measure it before building for it.

---

## 16. What `plan.md` keeps

`plan.md` Phase 2 keeps the schedule and the acceptance test: *you can ask something not in the
corpus and get a real web-sourced answer — and the agent chose the right lane on its own.* Its
capability checklists stay the checklists. This document owns the tools, the client, the guard, the
provenance model, the hygiene rules, the configuration, the module layout, and the eval mechanics.

One line of `plan.md` needs editing rather than merely being inherited — the Tavily-vs-Exa
measurement (§1) — and that edit is step 9 of §14.

---

## 17. What building it changed

Kept honest against the plan above, because a design document that is quietly wrong about the code
is worse than none. Four things moved.

### The trap question was designed, then dropped

§12 called for *"at least one **trap**: a question the corpus answers well but whose phrasing
invites a web search"*. It was written (`web-04`, *"what's the latest thinking on how deductibles
work?"*, labelled `reference`) and then removed before it ever ran. Two objections, the second
decisive:

1. It would have been the **31st reference question**, moving the recall@5 denominator — so Phase
   1-c's 0.800 would have stopped being comparable to Phase 2's. That is precisely the
   question-set-drift confusion §12 spends a paragraph warning about, arriving through the door the
   warning was not watching.
2. **It bought nothing.** `routing_correct` is scored on every non-abstaining question, so all
   thirty reference questions are *already* traps for over-reaching to the web — in bulk, and
   without touching any denominator.

The gold set carries the empty `web-04` slot and the reasoning, so nobody re-adds it. Final slice:
four web questions, five abstentions (`abs-04` converted as predicted), 43 questions total.

### The prompt's numbered list had to be centralised

§11 said the lane-dependent fragments should be composed rather than hand-written per combination.
Implementing that exposed a smaller problem the design had not noticed: the *step numbers* were
literals inside each fragment, so `_ANSWERING` needed telling how many steps preceded it
(`step=6 if structured else 4`) — one hand-maintained integer per lane combination, which the third
axis would have taken to eight. Steps are now bodies in a tuple and `_number()` renders the list.
A mis-numbered list is a small but real signal to the model that the instructions were not written
for the tools it has.

### `GoldSet.in_corpus()` was an exclusion list too

§12 identified `aggregate()`'s `!= "structured_api"` as an exclusion list that had to be inverted.
The same bug had a second instance the design missed: `GoldSet.in_corpus()` was
`not expected_abstain and not is_structured`, with the identical failure mode. Both are now
`== "reference"`, which no future lane can silently break.

### A pre-existing streaming bug surfaced, and was fixed here

Not this phase's work and not caused by it — recorded because it was found by this phase's smoke
target and fixed in this phase's diff. When the grounding validator rejects an answer and the retry
words the replacement differently, the reader saw the rejected draft followed by the accepted one.
`TokenEvent` gained an additive `reset: bool`. Full account: [agent.md](agent.md) §7.

It is worth noting *why* it survived from Phase 1a to here. It needs a retry **and** a materially
reworded second attempt; every offline test scripted retries whose second attempt was byte-identical
or a clean extension, so the whole suite was green. That is the case for keeping a rung that spends
one real call — `make smoke-web` found it on its first run, along with a second instance of the
"metric punishes a capability for existing" mistake that Phase 1-c had already made once.
