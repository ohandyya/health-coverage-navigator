# Health Coverage Navigator

Created by: Andy Tseng
Created time: July 3, 2026 2:55 PM
Status: In Progress
Tags: Learn

An open-source AI **agent** — one PydanticAI agent, built once and then grown a toolset at a
time — that answers U.S. health-coverage questions by routing between three tool "lanes":
the **indexed reference corpus** of public documents, **structured public APIs** for exact
facts, and **web search** for the fresh, open-ended world. Public data only.

The organizing idea: almost every health-coverage question decomposes into one of three
sub-types, and picking the right lane is the core engineering problem — not retrieval alone.
Each sub-question becomes an independently gradable unit with a known correct source-type,
which is directly borrowable from binary-decomposition eval thinking.

| Sub-question type | Example | Lane |
|---|---|---|
| "What does the rule/benefit say?" | *What is a deductible?* | **Reference** (the indexed corpus) |
| "What's the fact for this plan/drug/provider?" | *Is drug X covered under plan Y?* | **Structured API** |
| "What's happening now / not in my corpus?" | *Any recent recall on drug X?* | **Web search** |

**This is an agent, not a pipeline.** There is no fixed retrieve-then-answer chain anywhere in
the build. From Phase 1 onward there is a single PydanticAI `Agent` that decides which tools to
call, how often, and when it has enough to answer or must abstain. Each phase hands that same
agent more tools — full-text search, then vector search, then web search, then typed APIs — and
never replaces the agent underneath. What is written once in Phase 1 and then inherited: the
output schema, the provenance plumbing, the grounding/abstention rule, and the step limits.

Each phase below is independently shippable and has an **acceptance test** — the phase is done
when you can do the thing in the milestone line.

## Resources

- Google Drive: [folder](https://drive.google.com/drive/folders/1DR4dGU8eLrRXLk_Hsfz68sgAQ4ySMqp4?usp=drive_link)

## What does it do

Nearly everyone deals with health insurance, so impact is enormous. The public-data story is the
best in all of insurance. An indexed corpus of genuinely public reference material: the Medicare & You
handbook, CMS coverage determinations (the Medicare Coverage Database of national/local
determinations is a deep, structured, public corpus), ACA marketplace rules, and Summary of
Benefits & Coverage documents (which use a federally standardized public template). Live public
tools: the [Healthcare.gov](http://healthcare.gov/) Marketplace plan API, the NPI registry for
provider lookup, and openFDA / drug-pricing data. Web search for the fresh half — a specific
drug's recent news, a provider, a plan-year change. Answers things like "is this treatment
typically covered, what plans cover my doctor, and what changed for this plan year?" Growth path
is long: plan comparison → formulary/drug-cost lookup → provider-network checks → appeals
guidance under the No Surprises Act.

## Data sources (verified live; see licensing note)

### Bulk-downloadable corpus (vendored into the repo)

Two kinds live here, and the difference matters: the **text corpora** get chunked for the
reference lane's search tools (and embedded once Phase 1-b adds vectors), while the
**structured** sources land as a lossless columnar mirror that a later typed layer queries —
never chunked, never embedded.

**HealthCare.gov consumer-education content (the cleanest starting corpus).** HealthCare.gov
publishes every article and glossary term as machine-readable JSON, explicitly for third-party
reuse. All educational content is available in machine-readable formats, published as HTML pages
and JSON data, and everything is accessible through an API. There's a site-wide content index
endpoint plus per-post JSON (you append `.json` to any post URL). This is your best MVP corpus:
clean, unambiguously reusable, and written at exactly the "explain coverage to a human" level.
[HealthCare.gov](https://www.healthcare.gov/developers/)

**Medicare & You handbook + related CMS guides.** The annual handbook is a US-government work
(public domain), distributed as PDF. Great for the Medicare side of the domain. Pair it with the
HealthCare.gov glossary for terminology grounding.

**Medicare Coverage Database — National Coverage Determinations (NCDs).** Fetch these from the
**MCD Coverage API**, not the MCD Downloads page's bulk ZIPs. **Licensing caveat:** stick to NCDs
for your public corpus. NCDs don't contain procedure codes. The LCDs and Billing/Coding Articles
do — and CPT codes and similar are copyrighted by the AMA and ADA, with use restricted under
license. So the local-coverage code tables are the one thing you should *not* vendor into a public
GitHub repo. The API is what makes that rule self-enforcing: its auth boundary falls exactly on
the licensing boundary — National coverage endpoints answer without a key, LCD and Article
endpoints return `401` — so "NCDs only" becomes the set of endpoints that respond at all rather
than a rule the ingestion script has to police. The bulk ZIPs are the wrong route on both counts:
they 403 non-browser clients, and their single license click covers the AMA/ADA/AHA terms for
Local coverage data sitting beside the National data, which is precisely the conflation the
guardrail exists to prevent. See [medicare_ncd_data.md](medicare_ncd_data.md) for the endpoints,
the record schema, and the other license-clean National document types (NCAs, CALs, MEDCAC
materials, Technology Assessments) that are deliberately *not* vendored.
[CMS](https://www.cms.gov/medicare-coverage-database/downloads/downloads.aspx)
[Noridian](https://med.noridianmedicare.com/web/jea/policies/ncd)

**Health Insurance Exchange Public Use Files (the structured plan corpus).** These are the bulk
CSV/ZIP dumps behind the ACA marketplace, and they're actively maintained. The Exchange PUFs are
available for plan years 2014 through 2026, with the Benefits and Cost Sharing PUF updated April
28, 2026. The two you'll care about most: the Benefits and Cost Sharing PUF and the Plan
Attributes PUF. The Plan Attributes PUF contains plan-level data on max out-of-pocket,
deductibles, cost sharing, HSA eligibility, and formulary ID. These are large enough that the Rate
PUF and Benefits and Cost Sharing PUF exceed Excel's row limit and need a database or statistical
tool to open — good, because loading them into DuckDB/SQLite is exactly the kind of
structured-tool backend you want to practice against.
[CMS](https://www.cms.gov/marketplace/resources/data/public-use-files)

**Medicare Part D formulary files (the Part D structured corpus).** CMS publishes quarterly
formulary/pharmacy/pricing files. The Quarterly Prescription Drug Plan Formulary, Pharmacy
Network, and Pricing files contain formulary details including NDCs, cost-share tier, and
indicators for step therapy, quantity limits, and prior authorization — the Medicare-side
counterpart to the Exchange PUFs above, and the backing data for the drug-cost angle.

Scope worth stating up front, because the published file's shape forces a choice: it ships as a
2.49 GB container of ten nested per-file zips, of which the six-part pharmacy-network file is
92%. Seven files — formulary, excluded drugs, indication-based coverage, beneficiary cost,
insulin beneficiary cost, plan information, geographic locator — total 9.4 MB and cover every
question above. Take those; leave pharmacy-network for the Phase 5 provider/network work that
actually needs it, and pricing (191 MB) for when drug-cost estimates are on the table.
[CMS Data](https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers/quarterly-prescription-drug-plan-formulary-pharmacy-network-and-pricing-information)

#### Deliberately *not* bulk-downloaded

Two sources that have bulk downloads are **used live, through their APIs, and never vendored**.
Recorded here so neither gets re-added on the reasoning that "the bulk file exists":

- **NPPES provider registry.** The dissemination file exceeds 4 GB, and provider lookup is
  inherently one-record-at-a-time — *"what is this NPI's specialty"*, not "scan every provider
  in the country". A bulk mirror would be 4 GB of storage and a staleness problem in exchange
  for nothing the [live registry API](#live-web--api-tools) doesn't already answer, faster and
  fresher (it updates daily).
- **openFDA drug labels.** Same reasoning: the questions this project asks openFDA — *has drug X
  been recalled*, *what are its indications* — are per-drug lookups against a keyless API. Bulk
  JSON would only pay off for offline whole-corpus indexing, which is not a capability any phase
  plans.

**This has a consequence worth stating:** no provider-level data is ever vendored into this
repo, so the guardrail's PII surface stays limited to what the reference corpora happen to
contain. Both sources reappear as typed tools in [Phase 3](#phase-3--add-structured-api-tools).

### Live Web / API tools

This is the other two-thirds of your tool-routing problem: **structured public APIs**
(deterministic lookups) and **general web search** (the open-ended, freshest layer).

**Marketplace API** — the big one. This is the API that literally powers HealthCare.gov shopping.
The Marketplace API drives Window Shop and Plan Compare on HealthCare.gov, showing plans available
based on location and household, whether plans cover specific providers and drugs, and estimated
yearly costs. Interface highlights: [Cms](https://developer.cms.gov/marketplace-api/)

- Base: `https://marketplace.api.healthcare.gov/api/v1/`
- Drug autocomplete → RxCUI: `GET /drugs/autocomplete?q={query}&apikey={key}`
- Drug coverage check: `GET /drugs/covered?year={yr}&drugs={rxcui}&planids={planid}&apikey={key}`
- Plan search + cost estimates: `POST /households/eligibility/estimates`

You request a key via the CMS developer portal; note API keys are rate limited, with the limit
passed back in the response headers. There's also a companion **Finder API** for private health
plans available outside the Marketplace, with keys rate-limited to 1000 requests/minute and
expiring every 60 days. [Cms](https://developer.cms.gov/marketplace-api/)
[Cms](https://developer.cms.gov/finder-api/)

**openFDA** — drug facts, recalls, shortages. Base `https://api.fda.gov/`, no key needed to start.
Drug endpoints cover adverse events, product labeling, the NDC directory, recall enforcement
reports, the Orange Book, Drugs@FDA, and drug shortages. The label endpoint (`/drug/label.json`)
is the workhorse; it returns Structured Product Labeling data for prescription and OTC drugs,
broken into sections like indications, adverse reactions, and drug interactions.
[Fda](https://open.fda.gov/apis/drug/) [Fda](https://open.fda.gov/apis/drug/label/)

**NPPES NPI Registry** — live provider lookup at `https://npiregistry.cms.hhs.gov/api/`
(query-only, updated daily, no key). **This is the only route to provider data in this project**
— the 4 GB bulk file is deliberately not downloaded, for the reasons above.

**HealthCare.gov Content API** — doubles as a live tool: it's CORS-enabled and supports
cross-domain requests, returning content objects, collections, and a site-wide index. Handy for
pulling the freshest official explanation of a concept at query time.
[HealthCare.gov](https://www.healthcare.gov/developers/)

**General web search tool** — this is where the agent goes when the question isn't answerable
from the corpus or a structured endpoint: "any recent news on drug X," "did this insurer have a
market-conduct action," "what changed for the 2026 plan year that isn't in my index yet."
An **agent-oriented search API — Tavily or Exa** — rather than a scrape-and-parse SERP: both
return clean extracted content with source URLs, which is what the provenance requirement needs
and what a raw search-results page does not give. Wired up in
[Phase 2](#phase-2--add-the-web-search-tool); the choice between them is made there.

> ### ⚠️ Licensing note (matters because this repo is public)
> CMS **NCDs** and the **HealthCare.gov content** are freely reusable. But the Medicare Coverage
> Database **LCDs and Billing/Coding Articles embed AMA CPT/HCPCS and ADA CDT codes, which are
> copyrighted**. Keep those code tables **out** of the public repo — index NCDs only. The PUFs
> (Exchange and Part D) are public-domain and safe to vendor. openFDA and NPPES data would also
> be safe — NPPES is FOIA-disclosable even though it names real practitioners — but the question
> is moot here, since both are used live rather than vendored.

## Cross-cutting principles (apply from day one)

- [ ] **One agent, more tools.** Phase 1 stands up a single PydanticAI agent; every phase after
      it registers additional tools on that same agent rather than building a parallel system.
      A phase that would require rewriting the agent loop is a sign the phase is wrong, not the
      loop. The corollary: the agent's *scaffolding* — output schema, deps, provenance, grounding
      rule, step limits — is built once, in Phase 1, when there is exactly one lane to debug.
- [ ] **Eval harness is the through-line.** Each phase adds exactly one new *thing to grade*:
      retrieval → answer → routing → tri-modal routing → multi-hop + citations → regression. You
      want the harness before the agent, not after.
- [ ] **Source-type tagging.** Every claim in an answer is labeled by lane (reference /
      structured-API / web) with the retrieval or URL behind it. Introduced in Phase 1,
      formalized in Phase 4 — this is what keeps every later phase evaluable, and for a health
      tool it's a trust requirement, not polish.
- [ ] **Synthetic fixtures.** Keep a fixtures set of fake people / plans / drugs / ZIPs so
      demos and evals never depend on live API availability or touch anything sensitive.
- [ ] **Pin the plan year** in every query. CMS keeps multiple years live at once; mixing them
      silently is the most common correctness bug in this domain.
- [ ] **The UI tracks the phases, it doesn't lead them.** A FastAPI + web frontend is part of the
      build from Phase 0 onward, but each phase only adds the surface for the capability that
      phase ships. The API contract is fixed early (Phase 0) so that later phases add *values* to
      existing fields rather than reshaping the response. **All frontend design decisions,
      stack choices, API schemas, and UI specifics live in [frontend_plan.md](frontend_plan.md)
      — this document records only *when* each piece lands.**

## Architecture phases

### Phase 0 — Corpus + eval scaffold (before any agent)

Download and prepare the raw corpus — HealthCare.gov content JSON + Medicare & You + NCDs — into
a local `data/` directory (download → parse → chunk into `data/processed`). No embeddings, no
vector store yet. The chunking step's parameters, per-source strategy, and output contract are in
[chunking.md](chunking.md), written before Phase 1a so retrieval inherits the reasoning. In parallel, build a tiny gold eval set of ~30 questions with known answers and
known correct source-type. This pays off immediately: you want the corpus and the harness before
the agent, not after.

This is also where the **frontend groundwork** happens: freeze the API contract and stand up a
web UI against a stubbed answer endpoint. Doing it now — before there's an agent — means the
whole interface is proven while the stakes are zero, and Phase 1a only has to swap the stub for
a real agent. See [frontend_plan.md](frontend_plan.md) (Phase F0) for the stack, contract, and
layout; none of it is repeated here.

**Milestone / acceptance test:** you can run the ingestion pipeline and get a clean, chunked
corpus on disk, plus a gold eval set you can load and inspect — and a local web UI that renders
a stubbed answer end to end.

**User-facing capability**
- [ ] Run the ingestion pipeline and get a clean, chunked corpus written to `data/processed`
- [ ] Load and inspect the gold eval set (question → expected source-type → expected answer)
- [ ] Open the web UI locally and see a stubbed answer render with citations and a tool trace
- *(The "user" here is you-as-developer preparing data — no retrieval or synthesized answers yet.)*

**Software capability**
- [ ] Ingestion pipeline (download → parse → chunk → store to `data/processed`) that is idempotent and re-runnable
- [ ] Gold eval set (~30 questions), each tagged with expected source and answer
- [ ] Eval dataset loader / schema (so later phases can attach retrieval, answer, and routing metrics)
- [ ] **Frozen API contract** — request/response models carrying answer, citations, source-type, and abstention as first-class fields
- [ ] FastAPI app skeleton with a stubbed answer endpoint returning canned data
- [ ] Web UI scaffolded and rendering that stub

### Phase 1 — The agent itself, over the reference corpus

This is where the agentic system gets built, and it is the only phase that builds one. A single
PydanticAI `Agent`: a model named in `config.yaml`, typed dependencies, a structured output type,
a system prompt carrying the grounding rule, and a toolset over the reference corpus. It answers
coverage and terminology questions with citations back to chunks, and abstains when the question
falls outside the corpus. One lane — no web, no structured APIs — so that when the loop
misbehaves there is exactly one thing it could be. Ship it; this alone is useful.

What is deliberately *not* built here is a RAG pipeline. Nothing chains
retrieve → stuff-context → generate. The agent is handed search tools and left to decide how to
use them: which tool, what query, whether one call was enough, whether to widen the search or
abstain. That decision-making is the thing this project is about, which is why the tool trace is
worth looking at even in a one-lane phase.

Everything built here is scaffolding the later phases inherit rather than replace — output
schema, provenance plumbing, grounding guardrail, step limits. Later phases register more tools
on this same agent.

Split into two sub-phases: first prove the loop with the crudest search that could work, then
grow the toolset with vector search and let the agent choose between them.

#### Phase 1-a — full-text tools, no database

Give the agent a small toolset over `data/processed` built from plain full-text techniques —
directory listing, `grep`, keyword/BM25-style lexical search — with **no database of any kind**:
not a vector store, and not an embedded SQL or full-text engine either. Python's standard library
and the command line are the whole toolbox.

Narrow tools the agent composes itself, rather than one `retrieve(query)` that hides the search
strategy inside a function:

| Tool | What it does |
|---|---|
| `list_documents(source?)` | What is in the corpus at all — sources, titles, counts. Orients the agent, and is the honest basis for answering *"what can you tell me about?"* |
| `grep_corpus(pattern, source?)` | Literal / regex match over chunk text. For exact strings: a defined term, a plan name, an NCD number. |
| `search_corpus(query, k)` | Ranked lexical retrieval — BM25 over an in-memory inverted index. The general-purpose tool. |
| `get_chunk(chunk_id)` | Widen a hit to its neighbours in the parent document, for when a match lands mid-definition. |

Exact signatures settle in implementation; what is decided here is the *shape* — orient, match
exactly, rank, expand — and that there is more than one of them.

Two reasons for a toolset over a single call. It is the honest version of the exercise: an agent
that picks a tool, reads the result, and reformulates a failed query is doing the thing being
built, and its mistakes are legible in the trace instead of buried in a ranking function. And it
makes Phase 2's routing a change of degree rather than of kind — the agent is already choosing
between tools before a second lane exists.

BM25 stays in scope, because **BM25 is a ranking formula, not a storage engine**: term frequencies
in a `dict`, an inverted index built at startup, ~40 lines of stdlib. Measured over the real 6,722
chunks — 214 ms to build the index, 3–4 ms per query. Anything that would require a database to
implement is out; BM25 is not that.

**Milestone / acceptance test:** you can ask a coverage/terminology question **in the browser**
and get a cited answer sourced from the full-text tools over `data/processed`; it abstains when
the question is out of corpus; and the trace shows which tools the agent called, in what order,
with what arguments.

This is where the Phase 0 stub gets replaced by the real agent — the UI itself barely changes,
which is the point of having frozen the contract first. Frontend detail:
[frontend_plan.md](frontend_plan.md) (Phase F1).

**User-facing capability**
- [ ] Ask natural-language questions (*"what's a deductible?"*, *"does Medicare cover X?"*) and get a synthesized answer with citations to source documents
- [ ] Get an honest *"not in my reference material"* when the question is out of corpus — no hallucinated answer
- [ ] Do all of the above from the web UI, with the answer streaming in as it's generated
- [ ] Expand any citation to see the retrieved text behind it
- [ ] See which tools the agent chose and in what order — the trace is a user-facing feature from the first agent phase, not a debug view

**Software capability**
- [ ] PydanticAI `Agent`: model from `config.yaml`, typed deps (corpus handle), structured output type, system prompt carrying the grounding rule
- [ ] Full-text toolset over `data/processed` — `list_documents` / `grep_corpus` / `search_corpus` / `get_chunk` — with **no database at all**: no vector store, no DuckDB/SQLite FTS, no embeddings. Stdlib only.
- [ ] BM25 inverted index built in-process at startup, from `chunks.jsonl`
- [ ] Structured output populating the frozen contract's fields (answer + citations + claims + `abstained`)
- [ ] Chunk → source provenance plumbing
- [ ] Grounding guardrail: answer only from what the tools returned
- [ ] **Step / usage limits on the agent loop from day one** — cheap now, painful to retrofit; Phase 4 extends loop safety rather than introducing it
- [ ] Tool-call trace captured and surfaced through the `trace` field
- [ ] Eval set extended from retrieval-only to **answer correctness** and **faithfulness/groundedness**, now graded through the agent's own tool calls rather than a bare function call
- [ ] Stub endpoint replaced by the real agent; streaming response wired through to the UI
- [ ] Eval dashboard in the UI over real eval runs

#### Phase 1-b — add vector search alongside the full-text tools

Grow the toolset rather than swap it: add a `vector_search(query, k)` tool backed by embeddings in
a local vector store — **LanceDB**, embedded and on-disk, with embeddings computed by us and
handed over as plain vectors. **The Phase 1-a lexical tools stay registered.** Semantic and
lexical search sit side by side and the agent picks — or uses both and reconciles them. Same
agent, same corpus, same gold set; the only thing that changed is that there is now more than one
way to find a chunk, and choosing is the agent's problem.

LanceDB holds vector *and* BM25 search in one table, so the lexical and vector paths can share a
store instead of the comparison straddling two systems. See [lancedb.md](lancedb.md) for the full
rationale, the rejected alternatives (Chroma, pgvector, managed cloud services), and the
ingestion/query usage pattern.

The comparison is now three-way rather than a swap: run the gold set with the agent restricted to
lexical tools, to the vector tool, and to both. "Both" has to earn its place — it only wins if it
beats each alone, and the extra tool is also extra opportunity for the agent to choose badly.

**Milestone / acceptance test:** the same questions can now be answered through semantic
retrieval, and you can compare lexical-only, vector-only, and both-tools runs on the same gold
set.

**User-facing capability**
- [x] Same Q&A experience as Phase 1-a, now able to find chunks by meaning rather than wording
- [x] The trace shows *which* kind of search produced each citation (`TraceStep.tool`; no contract change was needed)

**Software capability**
- [x] Embedding model configured — same model for documents and queries, fixed dimensionality
- [x] LanceDB wired up, populated from `data/processed` (`make embed`, 6,722 vectors)
- [x] `vector_search` tool registered **alongside** the Phase 1-a tools, not in place of them
- [x] Toolset composition as an eval axis (which tools the agent is allowed to see), so lexical-only / vector-only / both is one runner with a flag rather than three code paths
- [x] Eval comparison across those three configurations on the same gold set (recall@k, MRR, answer correctness)
- [x] Eval dashboard gains a **run-comparison view** so the lexical-vs-vector-vs-both call is made from data, not vibes — *the only frontend work this phase needs; the chat UI is untouched by design*

**Measured (2026-08-16).** Vector beats lexical and "both" beats each alone, so it earned its
place. Retrieval only, deterministic, 30 in-corpus questions: BM25 0.567 recall@5 / 0.416 MRR
against vector **0.733 / 0.561**. Through the agent, 35 questions: lexical 0.667, vector 0.733,
both **0.800**. The decision was deliberately taken on the retrieval-only pair rather than an agent
A/B — the agent's known run-to-run spread is 0.200, wider than the effect. Detail and the
per-question breakdown: [agent.md](agent.md) §6.

### Phase 2 — Add the web-search tool

Grow the same agent with a second *lane*. Until now every tool it had pointed at the same corpus;
now it must decide whether the corpus is the right place at all. This is the first real routing
decision — "is this in my indexed reference material, or do I need the open web?" — and it is a
harder question than Phase 1-b's, because the wrong answer here is a confident abstention or a
web answer to something the corpus already settles. Add an eval slice specifically for routing
correctness (did it pick the right lane?), separate from answer correctness.

Use an **agent-oriented search API — Tavily or Exa** — not a scraped SERP. Both return extracted
page content with source URLs in one call, which is exactly what the per-claim provenance
requirement needs; scraping would mean owning an extraction pipeline that has nothing to do with
this project. Tavily and Exa differ in emphasis (Tavily leans Q&A-shaped answers with snippets,
Exa leans semantic/neural search over pages), so pick by measuring both on the out-of-corpus
slice of the gold set rather than by reputation. Either way the key is a secret in `.env` and the
tool is wrapped so a rate-limit or an outage degrades to an honest "couldn't check the web"
rather than a fabricated answer.

**Milestone / acceptance test:** you can ask something not in the corpus and get a real
web-sourced answer — and the agent chose the right lane on its own.

**User-facing capability**
- [ ] Ask time-sensitive / out-of-corpus questions (*"recent news on [drug]"*, *"2026 enrollment deadline"*) and get an answer
- [ ] See whether each answer came from reference material or the web

**Software capability**
- [ ] Web-search tool (Tavily or Exa) registered on the existing agent, with the key read server-side from `.env`
- [ ] Tool-choice behaviour where the agent decides reference-corpus vs. web — a system-prompt and tool-description problem, not a separate router component
- [ ] Source-type tagging in the output
- [ ] New eval slice measuring **routing correctness** (did it pick the right lane?), separate from answer correctness
- [ ] Basic web-result hygiene (dedupe, source filtering)
- [ ] UI: the `web` source badge goes live alongside `reference`, and routing accuracy joins the eval dashboard — see [frontend_plan.md](frontend_plan.md) (Phase F2)

### Phase 3 — Add structured-API tools

Grow the agent with the third lane: the Marketplace API (plan/drug/provider lookups), openFDA
(drug facts/recalls), and NPPES (provider lookup), wrapped as **typed tools** — Pydantic models
in and out, so a malformed API response is a validation error rather than plausible-looking
prose. Now it's genuinely tri-modal. The interesting failure mode to eval here: the agent
reaching for web search when a deterministic API would've given an exact answer, or vice versa.

**Milestone / acceptance test:** you can ask for exact facts about a specific plan, drug, or
provider and get a deterministic answer, not prose from a document.

**User-facing capability**
- [ ] Run precise lookups:
  - *"find plans in ZIP 30076 for a family of 3"*
  - *"is drug X covered under plan Y"*
  - *"what's this NPI's specialty"*
  - *"has drug X been recalled"*

**Software capability**
- [ ] Typed tool wrappers (Pydantic models) for Marketplace API, openFDA, and NPPES
- [ ] API-key / secrets management
- [ ] Rate-limit handling, retries, and a response cache
- [ ] Synthetic fixtures so tests/evals don't depend on live APIs
- [ ] Tri-modal routing (reference vs. structured-API vs. web) with an eval slice for it
- [ ] Schema validation on every API response
- [ ] UI: the `structured-API` badge goes live, completing the three-lane vocabulary; plan-year selector wired to every request — see [frontend_plan.md](frontend_plan.md) (Phase F2)

### Phase 4 — Multi-step agent + provenance

The agent has been calling tools since Phase 1; what it hasn't done is *plan*. Now it decomposes a
compound question into sub-questions, routes each to its own lane, and synthesizes one answer
from several tool results. Build in the habit from the start of tagging every claim in the final
answer by **source type** (indexed-reference vs. structured-API vs. web) with the retrieval/URL
behind it. For a health tool this isn't optional polish — it's what makes it trustworthy and what
makes it evaluable. Loop safety *tightens* here rather than appearing here: the step limits went
in with the agent in Phase 1-a, and this phase adds cycle detection and a hop ceiling on top of
them. Same loop-safety pattern as the appetite-engine reroute loop.

**Milestone / acceptance test:** you can ask a compound question that needs several lookups and
get one synthesized answer where every claim is traceable.

**User-facing capability**
- [ ] Ask multi-part questions (*"I take [drug] and live in [ZIP] — which marketplace plans cover it and what would they cost?"*) and get a single synthesized answer
- [ ] Inspect the tool trace to see how it got there
- [ ] Every claim carries a source-type label and the retrieval/URL behind it

**Software capability**
- [ ] Multi-step loop (plan → act → observe → synthesize) on the existing agent, within the Phase 1-a usage/step limits
- [ ] Question decomposition
- [ ] Per-claim provenance tagging
- [ ] Observability / tracing (Logfire or similar): tool calls, latencies, token usage
- [ ] Multi-hop correctness and citation-accuracy evals
- [ ] Loop safety: cycle detection + hop ceiling
- [ ] UI: trace panel handles nested multi-hop steps; hovering a claim highlights exactly the sources behind it — see [frontend_plan.md](frontend_plan.md) (Phase F3)

### Phase 5 — Growth surface

Once the tri-modal core is solid, the functionality tree is long: plan comparison across the PUFs,
formulary/drug-cost lookup, provider-network checks, appeals guidance under the No Surprises Act,
and a scheduled "what changed for this plan year" monitor (which turns the whole thing from a Q&A
bot into a monitoring product). Each is additive and doesn't disturb the core.

**Milestone / acceptance test:** it stops being a single-shot Q&A bot and becomes a tool —
comparisons, cost breakdowns, and scheduled monitoring.

**User-facing capability**
- [ ] Plan comparison tables
- [ ] Drug-cost breakdowns across the PUFs
- [ ] Provider-network checks
- [ ] Appeals guidance (e.g., No Surprises Act)
- [ ] Scheduled "what changed for this plan year" monitor that alerts on diffs

**Software capability**
- [ ] Structured backend for the PUFs (DuckDB / SQLite) with query tools over it
- [ ] Comparison / aggregation logic
- [ ] Scheduler for monitoring runs + state persistence to diff against
- [ ] Alert / output channel
- [ ] UI: comparison tables, cost breakdowns, and a "what changed" view — the first phase whose UI is more than chat + provenance — see [frontend_plan.md](frontend_plan.md) (Phase F4)
- [ ] Regression eval suite that grows with each capability so earlier phases don't silently break

## Suggested build order recap

```
           agent capability                     backend                        frontend
Phase 0    (no agent yet)                       corpus + gold eval set         contract frozen, UI on a stub
Phase 1a   the agent + full-text toolset        BM25 in memory, no DB          stub → real agent   [SHIPPABLE MVP]
Phase 1b   + vector search alongside            LanceDB                        eval run comparison
Phase 2    + web search (Tavily / Exa)          search API                     web badge + routing metrics
Phase 3    + typed structured-API tools         Marketplace / openFDA / NPPES  API badge   [tri-modal core complete]
Phase 4    + planning & decomposition           tracing / observability        multi-hop trace, per-claim highlight
Phase 5    (same agent, more domain tools)      DuckDB over the PUFs           tables + monitor    [product, not bot]
```

Read the first column downward: it is one agent gaining tools, never a rewrite. The tri-modal
core is complete at the end of Phase 3 — everything after that is additive and should not disturb
the core.

The frontend column is a schedule, not a spec. Stack, API schemas, UI layout, and the
corresponding F0–F4 checklists live in [frontend_plan.md](frontend_plan.md).
